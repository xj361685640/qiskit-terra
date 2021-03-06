# -*- coding: utf-8 -*-

# This code is part of Qiskit.
#
# (C) Copyright IBM 2019.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Test cases for the pulse utilities."""
import unittest

import numpy as np

from qiskit import pulse
from qiskit.pulse import Play, Delay, Acquire
from qiskit.pulse.channels import MeasureChannel, MemorySlot, DriveChannel, AcquireChannel
from qiskit.pulse.exceptions import PulseError
from qiskit.test import QiskitTestCase
from qiskit.test.mock import FakeOpenPulse2Q

from qiskit.pulse.reschedule import add_implicit_acquires, align_measures, pad


class TestAutoMerge(QiskitTestCase):
    """Test the helper function which aligns acquires."""

    def setUp(self):
        self.backend = FakeOpenPulse2Q()
        self.config = self.backend.configuration()
        self.inst_map = self.backend.defaults().instruction_schedule_map
        self.short_pulse = pulse.SamplePulse(samples=np.array([0.02739068], dtype=np.complex128),
                                             name='p0')

    def test_align_measures(self):
        """Test that one acquire is delayed to match the time of the later acquire."""
        sched = pulse.Schedule(name='fake_experiment')
        sched = sched.insert(0, Play(self.short_pulse, self.config.drive(0)))
        sched = sched.insert(1, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        sched = sched.insert(10, Acquire(5, self.config.acquire(1), MemorySlot(1)))
        sched = sched.insert(10, Play(self.short_pulse, self.config.measure(0)))
        sched = sched.insert(10, Play(self.short_pulse, self.config.measure(1)))
        sched = align_measures([sched], self.inst_map)[0]
        self.assertEqual(sched.name, 'fake_experiment')
        for time, inst in sched.instructions:
            if isinstance(inst, Acquire):
                self.assertEqual(time, 10)
        sched = align_measures([sched], self.inst_map, align_time=20)[0]
        for time, inst in sched.instructions:
            if isinstance(inst, Acquire):
                self.assertEqual(time, 20)
            if isinstance(inst.channels[0], MeasureChannel):
                self.assertEqual(time, 20)

    def test_align_post_u3(self):
        """Test that acquires are scheduled no sooner than the duration of the longest X gate.
        """
        sched = pulse.Schedule(name='fake_experiment')
        sched = sched.insert(0, Play(self.short_pulse, self.config.drive(0)))
        sched = sched.insert(1, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        sched = align_measures([sched], self.inst_map)[0]
        for time, inst in sched.instructions:
            if isinstance(inst, Acquire):
                self.assertEqual(time, 4)
        sched = align_measures([sched], self.inst_map, max_calibration_duration=10)[0]
        for time, inst in sched.instructions:
            if isinstance(inst, Acquire):
                self.assertEqual(time, 10)

    def test_multi_acquire(self):
        """Test that an error is raised if multiple acquires occur on the same channel."""
        sched = pulse.Schedule(name='fake_experiment')
        sched = sched.insert(0, Play(self.short_pulse, self.config.drive(0)))
        sched = sched.insert(4, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        sched = sched.insert(10, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        with self.assertRaises(PulseError):
            align_measures([sched], self.inst_map)

        # Test for measure channel
        sched = pulse.Schedule(name='fake_experiment')
        sched = sched.insert(10, Play(self.short_pulse, self.config.measure(0)))
        sched = sched.insert(30, Play(self.short_pulse, self.config.measure(0)))
        with self.assertRaises(PulseError):
            align_measures([sched], self.inst_map)

        # Test both using inst_map
        sched = pulse.Schedule()
        sched += self.inst_map.get('measure', (0, 1))
        align_measures([sched], align_time=50)
        sched += self.inst_map.get('measure', (0, 1))
        with self.assertRaises(PulseError):
            align_measures([sched], align_time=50)

    def test_error_post_acquire_pulse(self):
        """Test that an error is raised if a pulse occurs on a channel after an acquire."""
        sched = pulse.Schedule(name='fake_experiment')
        sched = sched.insert(0, Play(self.short_pulse, self.config.drive(0)))
        sched = sched.insert(4, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        # No error with separate channel
        sched = sched.insert(10, Play(self.short_pulse, self.config.drive(1)))
        align_measures([sched], self.inst_map)
        sched = sched.insert(10, Play(self.short_pulse, self.config.drive(0)))
        with self.assertRaises(PulseError):
            align_measures([sched], self.inst_map)

    def test_align_across_schedules(self):
        """Test that acquires are aligned together across multiple schedules."""
        sched1 = pulse.Schedule(name='fake_experiment')
        sched1 = sched1.insert(0, Play(self.short_pulse, self.config.drive(0)))
        sched1 = sched1.insert(10, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        sched2 = pulse.Schedule(name='fake_experiment')
        sched2 = sched2.insert(3, Play(self.short_pulse, self.config.drive(0)))
        sched2 = sched2.insert(25, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        schedules = align_measures([sched1, sched2], self.inst_map)
        for time, inst in schedules[0].instructions:
            if isinstance(inst, Acquire):
                self.assertEqual(time, 25)
        for time, inst in schedules[0].instructions:
            if isinstance(inst, Acquire):
                self.assertEqual(time, 25)


class TestAddImplicitAcquires(QiskitTestCase):
    """Test the helper function which makes implicit acquires explicit."""

    def setUp(self):
        self.backend = FakeOpenPulse2Q()
        self.config = self.backend.configuration()
        self.short_pulse = pulse.SamplePulse(samples=np.array([0.02739068], dtype=np.complex128),
                                             name='p0')
        sched = pulse.Schedule(name='fake_experiment')
        sched = sched.insert(0, Play(self.short_pulse, self.config.drive(0)))
        sched = sched.insert(5, Acquire(5, self.config.acquire(0), MemorySlot(0)))
        sched = sched.insert(5, Acquire(5, self.config.acquire(1), MemorySlot(1)))
        self.sched = sched

    def test_add_implicit(self):
        """Test that implicit acquires are made explicit according to the meas map."""
        sched = add_implicit_acquires(self.sched, [[0, 1]])
        acquired_qubits = set()
        for _, inst in sched.instructions:
            if isinstance(inst, Acquire):
                acquired_qubits.add(inst.acquire.index)
        self.assertEqual(acquired_qubits, {0, 1})

    def test_add_across_meas_map_sublists(self):
        """Test that implicit acquires in separate meas map sublists are all added."""
        sched = add_implicit_acquires(self.sched, [[0, 2], [1, 3]])
        acquired_qubits = set()
        for _, inst in sched.instructions:
            if isinstance(inst, Acquire):
                acquired_qubits.add(inst.acquire.index)
        self.assertEqual(acquired_qubits, {0, 1, 2, 3})

    def test_dont_add_all(self):
        """Test that acquires aren't added if no qubits in the sublist aren't being acquired."""
        sched = add_implicit_acquires(self.sched, [[4, 5], [0, 2], [1, 3]])
        acquired_qubits = set()
        for _, inst in sched.instructions:
            if isinstance(inst, Acquire):
                acquired_qubits.add(inst.acquire.index)
        self.assertEqual(acquired_qubits, {0, 1, 2, 3})

    def test_multiple_acquires(self):
        """Test for multiple acquires."""
        sched = pulse.Schedule()
        acq_q0 = pulse.Acquire(1200, AcquireChannel(0), MemorySlot(0))
        sched += acq_q0
        sched += acq_q0 << sched.duration
        sched = add_implicit_acquires(sched, meas_map=[[0]])
        self.assertEqual(sched.instructions, ((0, acq_q0), (2400, acq_q0)))


class TestPad(QiskitTestCase):
    """Test padding of schedule with delays."""

    def test_padding_empty_schedule(self):
        """Test padding of empty schedule."""
        self.assertEqual(pulse.Schedule(), pad(pulse.Schedule()))

    def test_padding_schedule(self):
        """Test padding schedule."""
        delay = 10
        sched = (Delay(delay, DriveChannel(0)).shift(10) +
                 Delay(delay, DriveChannel(0)).shift(10) +
                 Delay(delay, DriveChannel(1)).shift(10))

        ref_sched = (sched |
                     Delay(delay, DriveChannel(0)) |
                     Delay(delay, DriveChannel(0)).shift(20) |
                     Delay(delay, DriveChannel(1)) |
                     Delay(2 * delay, DriveChannel(1)).shift(20))

        self.assertEqual(pad(sched), ref_sched)

    def test_padding_schedule_inverse_order(self):
        """Test padding schedule is insensitive to order in which commands were added.

        This test is the same as `test_adding_schedule` but the order by channel
        in which commands were added to the schedule to be padded has been reversed.
        """
        delay = 10
        sched = (Delay(delay, DriveChannel(1)).shift(10) +
                 Delay(delay, DriveChannel(0)).shift(10) +
                 Delay(delay, DriveChannel(0)).shift(10))

        ref_sched = (sched |
                     Delay(delay, DriveChannel(0)) |
                     Delay(delay, DriveChannel(0)).shift(20) |
                     Delay(delay, DriveChannel(1)) |
                     Delay(2 * delay, DriveChannel(1)).shift(20))

        self.assertEqual(pad(sched), ref_sched)

    def test_padding_until_less(self):
        """Test padding until time that is less than schedule duration."""
        delay = 10

        sched = (Delay(delay, DriveChannel(0)).shift(10) +
                 Delay(delay, DriveChannel(1)))

        ref_sched = (sched |
                     Delay(delay, DriveChannel(0)) |
                     Delay(5, DriveChannel(1)).shift(10))

        self.assertEqual(pad(sched, until=15), ref_sched)

    def test_padding_until_greater(self):
        """Test padding until time that is greater than schedule duration."""
        delay = 10

        sched = (Delay(delay, DriveChannel(0)).shift(10) +
                 Delay(delay, DriveChannel(1)))

        ref_sched = (sched |
                     Delay(delay, DriveChannel(0)) |
                     Delay(30, DriveChannel(0)).shift(20) |
                     Delay(40, DriveChannel(1)).shift(10))

        self.assertEqual(pad(sched, until=50), ref_sched)

    def test_padding_supplied_channels(self):
        """Test padding of only specified channels."""
        delay = 10
        sched = (Delay(delay, DriveChannel(0)).shift(10) +
                 Delay(delay, DriveChannel(1)))

        ref_sched = (sched |
                     Delay(delay, DriveChannel(0)) |
                     Delay(2 * delay, DriveChannel(2)))

        channels = [DriveChannel(0), DriveChannel(2)]

        self.assertEqual(pad(sched, channels=channels), ref_sched)


if __name__ == '__main__':
    unittest.main()
