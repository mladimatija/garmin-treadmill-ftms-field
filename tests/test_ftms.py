"""
Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import unittest

import ftms_logic as L
from ftms_logic import Ftms, Targets, Fmt, CLOCK
import sim


def at_speed(w, kmh, tol=0.05):
    return abs(w.st.speedKmh - kmh) <= tol


class TestPackets(unittest.TestCase):
    def setUp(self):
        CLOCK.ms = 5000
        self.st = L.TreadmillState()

    def test_speed_distance_incline(self):
        # flags 0x040C: distance, inclination, elapsed; speed present (bit0 = 0)
        p = bytes([0x0C, 0x04, 0x32, 0x05, 0x10, 0x27, 0x00, 0x0A, 0x00, 0x00, 0x00, 0x3C, 0x00])
        got = Ftms.parseTreadmillData(p, self.st, False)
        self.assertEqual(got, Ftms.HAS_SPEED | Ftms.HAS_DISTANCE | Ftms.HAS_INCLINE)
        self.assertAlmostEqual(self.st.speedKmh, 13.3)
        self.assertAlmostEqual(self.st.incline, 1.0)
        self.assertEqual(self.st.machineElapsed, 60)

    def test_negative_and_unknown_incline(self):
        Ftms.parseTreadmillData(bytes([0x08, 0x00, 0xE8, 0x03, 0xF1, 0xFF, 0, 0]), self.st, False)
        self.assertAlmostEqual(self.st.incline, -1.5)
        self.st.hasIncline = False
        got = Ftms.parseTreadmillData(bytes([0x08, 0x00, 0xE8, 0x03, 0xFF, 0x7F, 0, 0]), self.st, False)
        self.assertFalse(self.st.hasIncline)
        self.assertEqual(got, Ftms.HAS_SPEED)

    def test_more_data_packet_without_speed(self):
        # "More Data" set: no speed, but energy (5 bytes) then heart rate
        p = bytes([0x81, 0x01, 0x2C, 0x01, 0xFF, 0xFF, 0xFF, 0x8C])
        got = Ftms.parseTreadmillData(p, self.st, False)
        self.assertEqual(got, 0)
        self.assertFalse(self.st.hasSpeed)
        self.assertEqual(self.st.machineEnergy, 300)
        self.assertEqual(self.st.machineHr, 140)

    def test_all_fields_offsets(self):
        # every field up to elapsed time, to check the byte offsets
        flags = 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080 | 0x0100 | 0x0200 | 0x0400
        p = bytes([flags & 0xFF, flags >> 8,
                   0xB0, 0x04,              # speed 12.00
                   0xAC, 0x04,              # avg speed
                   0xE8, 0x03, 0x00,        # distance 1000 m
                   0x14, 0x00, 0x00, 0x00,  # incline 2.0 %, ramp
                   0x64, 0x00, 0x00, 0x00,  # elevation +10.0 m
                   0x05, 0x05,              # inst pace, avg pace
                   0x50, 0x00, 0x00, 0x00, 0x00,  # energy 80 kcal
                   0x96,                    # HR 150
                   0x0A,                    # MET
                   0x2C, 0x01])             # elapsed 300 s
        got = Ftms.parseTreadmillData(p, self.st, False)
        self.assertEqual(got, 15)
        self.assertAlmostEqual(self.st.speedKmh, 12.0)
        self.assertAlmostEqual(self.st.incline, 2.0)
        self.assertEqual(self.st.machineEnergy, 80)
        self.assertEqual(self.st.machineHr, 150)
        self.assertEqual(self.st.machineElapsed, 300)

    def test_truncated(self):
        self.assertEqual(Ftms.parseTreadmillData(bytes([0x00]), self.st, False), -1)
        self.assertEqual(Ftms.parseTreadmillData(bytes([0x04, 0x00, 0xB0, 0x04, 0x01]), self.st, False),
                         Ftms.HAS_SPEED)

    def test_mph(self):
        Ftms.parseTreadmillData(bytes([0x00, 0x00, 0xEE, 0x02]), self.st, True)   # 7.50 mph
        self.assertAlmostEqual(self.st.speedKmh, 12.07, places=2)

    def test_ranges_and_feature(self):
        self.assertTrue(Ftms.parseSpeedRange(bytes([0x50, 0, 0xD0, 0x07, 0x0A, 0]), self.st, False))
        self.assertEqual((self.st.minSpeed, self.st.maxSpeed, self.st.speedInc), (0.8, 20.0, 0.1))
        self.assertTrue(Ftms.parseInclineRange(bytes([0, 0, 0x96, 0, 0x05, 0]), self.st))
        self.assertEqual((self.st.minIncline, self.st.maxIncline, self.st.inclineInc), (0.0, 15.0, 0.5))
        self.assertFalse(Ftms.parseSpeedRange(bytes([0, 0, 0, 0, 0, 0]), self.st, False))
        self.assertEqual(Ftms.parseFeature(bytes([0x0C, 0x14, 0, 0, 0x03, 0, 0, 0])), [0x140C, 3])


class TestAdvertising(unittest.TestCase):
    def test_raw_ad_parsing(self):
        flags = bytes([0x02, 0x01, 0x06])
        self.assertTrue(Ftms.rawHasFtms(flags + bytes([0x05, 0x03, 0x0D, 0x18, 0x26, 0x18])))   # 16-bit list
        self.assertTrue(Ftms.rawHasFtms(flags + bytes([0x06, 0x16, 0x26, 0x18, 0x01, 0x01, 0x00])))  # service data
        base = bytes([0xFB, 0x34, 0x9B, 0x5F, 0x80, 0x00, 0x00, 0x80, 0x00, 0x10, 0x00, 0x00, 0x26, 0x18, 0x00, 0x00])
        self.assertTrue(Ftms.rawHasFtms(flags + bytes([0x11, 0x07]) + base))                     # 128-bit list
        self.assertFalse(Ftms.rawHasFtms(flags + bytes([0x03, 0x03, 0x0D, 0x18])))               # heart rate only
        self.assertFalse(Ftms.rawHasFtms(flags + bytes([0x09, 0x03, 0x26])))                     # truncated
        self.assertFalse(Ftms.rawHasFtms(bytes([])))
        self.assertFalse(Ftms.rawHasFtms(None))
        self.assertFalse(Ftms.rawHasFtms(flags + bytes([0x00, 0x00, 0x00])))                     # zero padding


class TestCommands(unittest.TestCase):
    def test_encode(self):
        self.assertEqual(Ftms.encodeSpeed(12.0, False), bytes([0x02, 0xB0, 0x04]))
        self.assertEqual(Ftms.encodeSpeed(13.3, False), bytes([0x02, 0x32, 0x05]))
        self.assertEqual(Ftms.encodeSpeed(10.0, False), bytes([0x02, 0xE8, 0x03]))
        self.assertEqual(Ftms.encodeIncline(1.0), bytes([0x03, 0x0A, 0x00]))
        self.assertEqual(Ftms.encodeIncline(-1.5), bytes([0x03, 0xF1, 0xFF]))
        self.assertEqual(Ftms.encodeSpeed(12.07, True), bytes([0x02, 0xEE, 0x02]))


class TestTargets(unittest.TestCase):
    def test_notes(self):
        n = "Treadmill 13.3 km/h, 1% incline. Threshold pace (2 km)"
        self.assertAlmostEqual(Targets.speedFromText(n), 13.3)
        self.assertAlmostEqual(Targets.inclineFromText(n), 1.0)
        self.assertAlmostEqual(Targets.speedFromText("TM 12,5 KPH @ 2 %"), 12.5)
        self.assertAlmostEqual(Targets.inclineFromText("TM 12,5 KPH @ 2 %"), 2.0)
        self.assertAlmostEqual(Targets.speedFromText("7.5 mph"), 12.07, places=2)
        self.assertAlmostEqual(Targets.speedFromText("easy 10km/h"), 10.0)
        self.assertAlmostEqual(Targets.inclineFromText("downhill -1% grade"), -1.0)
        self.assertAlmostEqual(Targets.inclineFromText("incline 8.6%."), 8.6)
        self.assertIsNone(Targets.speedFromText("Easy jog"))
        self.assertIsNone(Targets.speedFromText(None))
        self.assertIsNone(Targets.inclineFromText("no incline given"))
        self.assertIsNone(Targets.speedFromText("km/h"))
        self.assertIsNone(Targets.speedFromText("0 km/h"))
        self.assertAlmostEqual(Targets.speedFromText("(2 km, 13.3 km/h)"), 13.3)

    def test_pace_targets_give_the_table_speeds(self):
        st = L.TreadmillState()
        for fast, slow, kmh in (("4:55", "5:05", 12.0), ("4:28", "4:34", 13.3), ("5:50", "6:10", 10.0)):
            v = Targets.speedFromTarget(0, sim.pace_mps(slow), sim.pace_mps(fast))
            self.assertAlmostEqual(Targets.clampSpeed(v, st, 16.0), kmh, places=6)

    def test_pace_target_edge_cases(self):
        self.assertIsNone(Targets.speedFromTarget(1, 3.0, 3.5))       # heart rate target
        self.assertIsNone(Targets.speedFromTarget(0, 0, 0))
        self.assertIsNone(Targets.speedFromTarget(0, 3, 3))           # zone number, not a speed
        self.assertAlmostEqual(Targets.speedFromTarget(0, 3649, 3731), 13.286, places=2)   # mm/s
        self.assertIsNone(Targets.speedFromTarget(0, 0, 3.0))       # open-ended: no guessing

    def test_interval_step(self):
        a = L.WorkoutStep(0, sim.pace_mps("4:34"), sim.pace_mps("4:28"))
        r = L.WorkoutStep(0, sim.pace_mps("6:10"), sim.pace_mps("5:50"))
        info = L.WorkoutStepInfo("Run", None, 0, L.WorkoutIntervalStep(a, r))
        self.assertAlmostEqual(Targets.speedFromStep(info), 13.29, places=2)
        info.intensity = 1
        self.assertAlmostEqual(Targets.speedFromStep(info), 10.01, places=2)

    def test_clamp(self):
        st = L.TreadmillState()
        self.assertAlmostEqual(Targets.clampSpeed(25.0, st, 16.0), 16.0)
        self.assertAlmostEqual(Targets.clampSpeed(0.2, st, 16.0), 0.8)
        self.assertAlmostEqual(Targets.clampIncline(0.7, st, 8.0), 0.5)
        self.assertAlmostEqual(Targets.clampIncline(0.8, st, 8.0), 1.0)
        self.assertAlmostEqual(Targets.clampIncline(20.0, st, 8.0), 8.0)
        self.assertAlmostEqual(Targets.clampIncline(-3.0, st, 8.0), 0.0)
        st.maxIncline = 7.9
        self.assertLessEqual(Targets.clampIncline(9.0, st, 8.0), 7.9)

    def test_pace_format(self):
        self.assertEqual(Fmt.pace(13.3), "4:31")
        self.assertEqual(Fmt.pace(12.0), "5:00")
        self.assertEqual(Fmt.pace(10.0), "6:00")
        self.assertEqual(Fmt.pace(0.0), "--:--")


class TestSessions(unittest.TestCase):
    """Whole sessions against the simulated treadmill."""

    def gym(self, machine=None, workout=None, cfg=None, others=True, profile_limit=6):
        m = machine or sim.FakeTreadmill()
        machines = [m]
        if others:     # four more Matrix treadmills further away
            machines += [sim.FakeTreadmill(name="MATRIX-%d" % i, rssi=-70 - i) for i in range(4)]
        w = sim.Watch(machines, workout, cfg, profile_limit)
        return w, m

    def warm_up_and_start(self, w, m):
        w.run(12)                                     # connect while standing on the belt
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        m.console_start(1.0)                          # Quick Start on the console
        w.run(3)
        m.console_speed(3.0)                          # walking
        w.run(5)
        w.timer_start()                               # Start on the watch

    def test_connects_to_the_closest_treadmill(self):
        w, m = self.gym()
        w.run(12)
        self.assertTrue(w.mgr.isReady())
        self.assertEqual(w.mgr.deviceName, "MATRIX-12")
        self.assertTrue(m.connected)
        self.assertEqual(sum(1 for x in w.machines if x.connected), 1)
        self.assertEqual((w.st.maxSpeed, w.st.inclineInc), (20.0, 0.5))

    def test_interval_workout_follows_every_step(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        speeds_mid_step = []
        marks = [7.5 * 60, 15 * 60 + 4.5 * 60, 15 * 60 + 9 * 60 + 60]   # middle of WU, run 1, rec 1
        t = 0
        for mark in marks:
            w.run(mark - t)
            t = mark
            speeds_mid_step.append(round(m.speed, 2))
        self.assertEqual(speeds_mid_step, [12.0, 13.3, 10.0])
        self.assertAlmostEqual(m.incline, 1.0, places=1)
        w.run(63 * 60 - t + 5)
        cmds = [c for c in w.commands(m) if c[0] in ("speed", "incline")]
        speeds = [c[1] for c in cmds if c[0] == "speed"]
        self.assertEqual(speeds, [12.0, 13.3, 10.0, 13.3, 10.0, 13.3, 10.0, 12.0])
        self.assertTrue(all(c[1] == 1.0 for c in cmds if c[0] == "incline"))
        self.assertEqual(w.commands(m)[0], ("control",))
        self.assertNotIn(("start",), w.commands(m))   # never starts the belt itself
        # treadmill distance this activity, timer time only (about 12.9 km for this session)
        self.assertGreater(w.st.sessDist, 12700)
        self.assertLess(w.st.sessDist, 13050)
        self.assertAlmostEqual(w.st.sessAscent, w.st.sessDist * 0.01, delta=8)
        self.assertFalse(w.follower.inWorkout)         # workout finished: field lets go
        modes = set(x[1] for x in w.log)
        self.assertNotIn("MANUAL", modes)

    def test_pace_targets_only_same_result(self):
        w, m = self.gym(workout=sim.interval_workout(False))
        self.warm_up_and_start(w, m)
        w.run(64 * 60)
        speeds = [c[1] for c in w.commands(m) if c[0] == "speed"]
        self.assertEqual(speeds, [12.0, 13.3, 10.0, 13.3, 10.0, 13.3, 10.0, 12.0])
        self.assertEqual(w.follower.stepSource, "")

    def test_console_change_is_respected_until_next_step(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(15 * 60 + 120)                    # 2 min into run 1
        m.console_speed(13.0)                   # runner backs off on the console
        w.run(30)
        self.assertEqual(w.follower.modeText(), "MANUAL")
        n_before = len(w.commands(m))
        w.run(5 * 60)
        self.assertAlmostEqual(m.speed, 13.0, places=1)       # left alone
        self.assertEqual(len(w.commands(m)), n_before)
        w.run(2 * 60 + 30)                       # into the recovery
        self.assertEqual(w.follower.modeText(), "AUTO")
        self.assertAlmostEqual(m.speed, 10.0, places=1)

    def test_override_detected_without_status_messages(self):
        m = sim.FakeTreadmill(status_on_target=False)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        m.console_speed(11.0)
        w.run(25)
        self.assertEqual(w.follower.modeText(), "MANUAL")
        self.assertAlmostEqual(m.speed, 11.0, places=1)

    def test_slow_ramping_machine_is_not_an_override(self):
        m = sim.FakeTreadmill(status_on_target=False, accel=0.3, incline_rate=0.1, incline_step=0.5)
        w, m = self.gym(machine=m, workout=sim.Workout([
            sim.step("Warm Up", "Treadmill 12.0 km/h, 6% incline", 2, 120),
            sim.step("Run", "Treadmill 13.3 km/h, 0% incline", 0, 120),
            sim.step("Walk", "Treadmill 4.0 km/h, 3% incline", 4, 120)]))
        self.warm_up_and_start(w, m)
        w.run(6 * 60)
        self.assertNotIn("MANUAL", set(x[1] for x in w.log))
        speeds = [c[1] for c in w.commands(m) if c[0] == "speed"]
        self.assertEqual(speeds, [12.0, 13.3, 4.0])

    def test_sticky_manual_when_auto_resume_off(self):
        cfg = L.FieldSettings(autoResume=False)
        w, m = self.gym(workout=sim.interval_workout(True), cfg=cfg)
        self.warm_up_and_start(w, m)
        w.run(60)
        m.console_speed(11.5)
        w.run(30)
        w.run(15 * 60)
        self.assertEqual(w.follower.modeText(), "MANUAL")
        self.assertAlmostEqual(m.speed, 11.5, places=1)

    def test_no_commands_before_watch_start_or_while_paused(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        w.run(12)
        m.console_start(1.0)
        w.run(30)
        self.assertEqual(w.commands(m), [])          # belt running, watch not started
        w.timer_start()
        w.run(10)
        self.assertTrue(at_speed(w, 12.0))
        w.timer_pause()
        m.console_speed(4.0)
        w.run(60)
        self.assertEqual([c for c in w.commands(m) if c[0] == "pause"], [])
        self.assertAlmostEqual(m.speed, 4.0, places=1)    # watch paused: belt untouched
        w.timer_resume()
        w.run(10)
        self.assertAlmostEqual(m.speed, 12.0, places=1)   # resumed: back to the step target

    def test_safety_key(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(120)
        m.pull_safety_key()
        w.run(60)
        self.assertEqual(m.speed, 0.0)
        self.assertNotIn(("start",), w.commands(m))
        m.console_start(1.0)                          # key back in, Quick Start
        w.run(15)
        self.assertAlmostEqual(m.speed, 12.0, places=1)   # timer still running: back to target

    def test_belt_never_started_from_watch_by_default(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        w.run(12)
        w.timer_start()
        w.run(60)
        self.assertEqual(m.speed, 0.0)
        self.assertEqual(w.commands(m), [])

    def test_remote_start_when_enabled(self):
        cfg = L.FieldSettings(remoteStart=True)
        w, m = self.gym(workout=sim.interval_workout(True), cfg=cfg)
        w.run(12)
        w.timer_start()
        w.run(15)
        self.assertIn(("start",), w.commands(m))
        self.assertAlmostEqual(m.speed, 12.0, places=1)
        m.console_stop()                              # stopping on the console is final
        w.run(30)
        self.assertEqual(w.commands(m).count(("start",)), 1)
        self.assertEqual(round(m.speed, 1), 0.0)

    def test_pause_with_watch_when_enabled(self):
        cfg = L.FieldSettings(pauseWithWatch=True)
        w, m = self.gym(workout=sim.interval_workout(True), cfg=cfg)
        self.warm_up_and_start(w, m)
        w.run(30)
        w.timer_pause()
        w.run(15)
        self.assertIn(("pause",), w.commands(m))
        self.assertEqual(round(m.speed, 1), 0.0)

    def test_read_only_treadmill(self):
        m = sim.FakeTreadmill(target_bits=0x0C)       # says: speed/incline targets not supported
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        self.assertEqual(w.commands(m), [])
        self.assertEqual(w.follower.modeText(), "READ ONLY")
        self.assertGreater(w.st.sessDist, 0)

    def test_machine_that_needs_ftms_start(self):
        m = sim.FakeTreadmill(needs_ftms_start=True)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(20)
        self.assertIn(("start",), w.commands(m))      # belt already moving, so harmless
        self.assertAlmostEqual(m.speed, 12.0, places=1)
        self.assertAlmostEqual(m.incline, 1.0, places=1)    # incline must not be lost either
        self.assertEqual(w.follower.modeText(), "AUTO")

    def test_machine_without_indications(self):
        m = sim.FakeTreadmill(indications=False)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(30)
        self.assertAlmostEqual(m.speed, 12.0, places=1)
        self.assertAlmostEqual(m.incline, 1.0, places=1)

    def test_control_refused(self):
        m = sim.FakeTreadmill(control_allowed=False)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(30)
        self.assertEqual(w.follower.modeText(), "NO CONTROL")
        self.assertAlmostEqual(m.speed, 3.0, places=1)

    def test_split_packets_and_no_distance_field(self):
        m = sim.FakeTreadmill(split_packets=True, has_distance=False)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(10 * 60)
        self.assertFalse(w.st.hasMachineDistance)
        self.assertAlmostEqual(w.st.sessDist, m.distance, delta=120)   # integrated from speed

    def test_smaller_profile_when_watch_refuses_six_characteristics(self):
        w, m = self.gym(workout=sim.interval_workout(True), profile_limit=3)
        self.warm_up_and_start(w, m)
        w.run(20)
        self.assertEqual(w.mgr.profileLevel, 2)
        self.assertAlmostEqual(m.speed, 12.0, places=1)

    def test_reconnect_after_dropout(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        m.on_disconnect()                     # console drops the link
        w.run(25)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        m.console_speed(12.0)
        w.run(20)
        self.assertGreater(len([c for c in w.commands(m) if c[0] == "control"]), 1)

    def test_auto_lap_does_not_undo_a_console_change(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(15 * 60 + 60)                     # 1 min into run 1
        m.console_speed(13.0)
        w.run(20)
        self.assertEqual(w.follower.modeText(), "MANUAL")
        n = len(w.commands(m))
        w.auto_lap()                            # Auto Lap at the next km
        w.run(60)
        self.assertEqual(w.follower.modeText(), "MANUAL")
        self.assertEqual(len(w.commands(m)), n)
        self.assertAlmostEqual(m.speed, 13.0, places=1)

    def test_lap_key_moves_to_next_step(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        w.lap()                                 # skip the rest of the warm-up
        w.run(15)
        self.assertEqual(w.follower.targetSpeed, 13.3)
        self.assertAlmostEqual(m.speed, 13.3, places=1)

    def test_reconnect_keeps_a_console_change(self):
        w, m = self.gym(workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        m.console_speed(11.0)
        w.run(20)
        self.assertEqual(w.follower.modeText(), "MANUAL")
        m.on_disconnect()
        w.run(30)
        self.assertTrue(w.mgr.isReady())
        self.assertAlmostEqual(m.speed, 11.0, places=1)    # not pushed back to 12.0
        self.assertNotIn(("speed", 12.0), w.commands(m)[3:])

    def test_incline_override_detected_from_data(self):
        m = sim.FakeTreadmill(status_on_target=False)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        m.console_incline(3.0)
        w.run(30)
        self.assertTrue(w.follower.overrideIncline)
        self.assertFalse(w.follower.overrideSpeed)
        self.assertAlmostEqual(m.incline, 3.0, places=1)

    def test_remote_start_never_fires_late(self):
        cfg = L.FieldSettings(remoteStart=True, rssiMin=-70)
        m = sim.FakeTreadmill(rssi=-80)                   # too far at first
        w, m = self.gym(machine=m, workout=sim.interval_workout(True), cfg=cfg, others=False)
        w.run(5)
        w.timer_start()                                   # Start pressed while not connected
        w.run(30)
        self.assertFalse(w.mgr.isReady())
        m.rssi = -50                                      # walks up to the treadmill
        w.run(30)
        self.assertTrue(w.mgr.isReady())
        self.assertNotIn(("start",), w.commands(m))
        self.assertEqual(m.speed, 0.0)

    def test_refused_speed_is_reported_not_called_manual(self):
        m = sim.FakeTreadmill(refuse_speed=True, status_on_target=False)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        self.assertEqual(w.follower.modeText(), "REFUSED")
        self.assertEqual(w.follower.refusedText(), "Speed Refused")
        self.assertAlmostEqual(m.speed, 3.0, places=1)
        self.assertAlmostEqual(m.incline, 1.0, places=1)    # incline still works

    def test_waits_for_characteristic_discovery(self):
        m = sim.FakeTreadmill(discovery_delay=2500)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        w.run(15)
        self.assertTrue(w.mgr.isReady())
        self.assertIn("2acd", m.notify)
        self.assertTrue(w.st.isFresh())
        self.assertEqual((w.st.maxSpeed, w.st.inclineInc), (20.0, 0.5))

    def test_safety_key_shown_while_belt_coasts_down(self):
        m = sim.FakeTreadmill(safety_instant=False)
        w, m = self.gym(machine=m, workout=sim.interval_workout(True))
        self.warm_up_and_start(w, m)
        w.run(60)
        m.pull_safety_key()
        w.run(3)
        self.assertTrue(w.st.safetyKey)                   # still slowing down: flag stays
        w.run(20)
        self.assertTrue(w.st.safetyKey)
        self.assertEqual(round(m.speed, 1), 0.0)
        m.console_start(1.0)
        w.run(15)
        self.assertFalse(w.st.safetyKey)
        self.assertAlmostEqual(m.speed, 12.0, places=1)

    def test_safety_key_cleared_when_quiet_machine_restarts(self):
        st = L.TreadmillState()
        CLOCK.ms = 10000
        st.setSpeed(12.0)
        st.safetyKey = True                 # status 0x03 arrives
        CLOCK.ms = 11000
        st.setSpeed(6.0)                    # still coasting: flag stays
        self.assertTrue(st.safetyKey)
        CLOCK.ms = 60000                    # machine sent nothing while stopped
        st.setSpeed(1.0)                    # restarted
        self.assertFalse(st.safetyKey)

    def test_found_via_raw_advertising_only(self):
        w, m = self.gym(machine=sim.FakeTreadmill(advert="raw"), others=False)
        w.run(12)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())

    def test_found_via_service_data_only(self):
        w, m = self.gym(machine=sim.FakeTreadmill(advert="servicedata"), others=False)
        w.run(12)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())

    def test_found_by_name_when_advert_shows_nothing(self):
        m = sim.FakeTreadmill(name="MATRIX T50", advert="none")
        w, m = self.gym(machine=m, others=False)
        w.run(6)
        self.assertFalse(w.mgr.isReady())                 # waits for real FTMS adverts first
        w.run(12)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())

    def test_matrix_console_found_by_serial_name(self):
        # Matrix commercial consoles advertise their serial number as the name
        m = sim.FakeTreadmill(name="CTM551L0000K00001", rssi=-60, advert="none")
        w, m = self.gym(machine=m, others=False)
        w.run(14)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        self.assertEqual(w.mgr.deviceName, "CTM551L0000K00001")

    def test_matrix_console_advertising(self):
        # as a Matrix console shows in nRF Connect: 0x1826 listed and service data 01 01 00
        m = sim.FakeTreadmill(name="CTM551L0000K00001", rssi=-60, advert="uuids")
        w, m = self.gym(machine=m, others=False)
        w.run(12)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())

    def test_name_guess_that_is_not_ftms_is_skipped(self):
        bike = sim.FakeTreadmill(name="Matrix Bike", rssi=-50, advert="none", ftms=False)
        tm = sim.FakeTreadmill(name="MATRIX-7", rssi=-60, advert="none")
        w = sim.Watch([bike, tm])
        w.run(60)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        self.assertEqual(w.mgr.deviceName, "MATRIX-7")
        self.assertTrue(tm.connected)
        self.assertFalse(bike.connected)

    def test_proven_ftms_beats_name_guess(self):
        guess = sim.FakeTreadmill(name="Treadmill speaker", rssi=-45, advert="none", ftms=False)
        real = sim.FakeTreadmill(name="X", rssi=-70)
        w = sim.Watch([guess, real])
        w.run(12)
        self.assertEqual(w.mgr.deviceName, "X")
        self.assertFalse(guess.connected)

    def test_slow_first_setup_of_real_treadmill_is_retried(self):
        m = sim.FakeTreadmill(discovery_delay=12000)    # first connection: setup times out
        w, m = self.gym(machine=m, others=False)
        w.run(20)
        self.assertFalse(w.mgr.isReady())
        m.discovery_delay = 0
        w.run(30)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        self.assertEqual(w.mgr.excluded, [])

    def test_diagnostics_when_no_treadmill_advertises(self):
        # too far away to be worth a blind probe
        phone = sim.FakeTreadmill(name="iPhone", rssi=-82, advert="none", ftms=False)
        pods = sim.FakeTreadmill(name="AirPods Pro 2", rssi=-86, advert="none", ftms=False)
        w = sim.Watch([phone, pods])
        w.run(5)
        self.assertEqual(w.mgr.statusText(), "Searching...")
        w.run(10)
        self.assertEqual(w.mgr.statusText(), "No FTMS (2 seen)")
        self.assertEqual(w.mgr.diagText(), "iPhone -82  AirPods Pr -86")
        w.run(60)
        self.assertEqual(phone.pair_attempts + pods.pair_attempts, 0)

    def test_matrix_console_with_name_and_ftms_only_in_scan_response(self):
        # what a Matrix console looks like from the watch: no names, nothing FTMS in the adverts.
        # The console is found by connecting to the strongest devices one by one.
        phone = sim.FakeTreadmill(name="iPhone", rssi=-48, advert="none", ftms=False,
                                  name_visible=False, connectable=False)
        band = sim.FakeTreadmill(name="Band", rssi=-55, advert="none", ftms=False, name_visible=False)
        console = sim.FakeTreadmill(name="CTM551L0000K00001", rssi=-60, advert="none", name_visible=False)
        far = [sim.FakeTreadmill(name="x%d" % i, rssi=-84 - i, advert="none", ftms=False, name_visible=False)
               for i in range(5)]
        w = sim.Watch([phone, band, console] + far)
        w.run(9)
        self.assertEqual(w.mgr.statusText(), "Searching...")
        self.assertEqual(w.mgr.diagText(), "8 unnamed -48 -55")
        w.run(4)
        self.assertEqual(w.mgr.statusText(), "Checking -48")
        w.run(120)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        self.assertTrue(console.connected)
        self.assertEqual(phone.pair_attempts, 1)      # tried once, then left alone
        self.assertEqual(band.pair_attempts, 1)
        self.assertEqual(sum(m.pair_attempts for m in far), 0)
        self.assertEqual(w.mgr.statusText(), "Treadmill")

    def test_name_matched_console_is_retried_after_connect_timeout(self):
        # e.g. a phone held the console's one connection for a moment
        m = sim.FakeTreadmill(name="CTM551L0000K00001", rssi=-60, advert="none", connectable=False)
        w = sim.Watch([m])
        w.run(40)
        self.assertFalse(w.mgr.isReady())
        m.connectable = True
        w.run(60)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        self.assertEqual(w.mgr.excluded, [])

    def test_confirmed_console_reconnects_without_probing_again(self):
        phone = sim.FakeTreadmill(name="p", rssi=-48, advert="none", ftms=False, name_visible=False)
        console = sim.FakeTreadmill(name="c", rssi=-60, advert="none", name_visible=False)
        w = sim.Watch([phone, console])
        w.run(120)
        self.assertTrue(w.mgr.isReady())
        console.on_disconnect()                     # the console drops the link (e.g. its session ends)
        w.run(60)
        self.assertTrue(w.mgr.isReady(), w.mgr.statusText())
        self.assertEqual(phone.pair_attempts, 1)

    def test_probing_stops_after_six_devices(self):
        devs = [sim.FakeTreadmill(name="d%d" % i, rssi=-50 - i, advert="none", ftms=False, name_visible=False)
                for i in range(9)]
        w = sim.Watch(devs)
        w.run(400)
        self.assertEqual(sum(m.pair_attempts for m in devs), 6)
        self.assertFalse(w.mgr.isReady())
        self.assertEqual(w.mgr.statusText(), "No FTMS (9 seen)")

    def test_name_filter_turns_probing_off(self):
        console = sim.FakeTreadmill(name="c", rssi=-60, advert="none", name_visible=False)
        cfg = L.FieldSettings(nameFilter="00001")
        w = sim.Watch([console], None, cfg)
        w.run(90)
        self.assertEqual(console.pair_attempts, 0)

    def test_diagnostics_when_nothing_in_range(self):
        w = sim.Watch([])
        w.run(15)
        self.assertEqual(w.mgr.statusText(), "No BLE devices")
        self.assertEqual(w.mgr.diagText(), "")

    def test_name_filter(self):
        cfg = L.FieldSettings(nameFilter="matrix-2")
        w, m = self.gym(cfg=cfg)
        w.run(12)
        self.assertEqual(w.mgr.deviceName, "MATRIX-2")

    def test_too_far(self):
        cfg = L.FieldSettings(rssiMin=-50)
        w, m = self.gym(cfg=cfg)
        w.run(12)
        self.assertEqual(w.mgr.link, L.LINK_SCANNING)
        self.assertEqual(w.mgr.statusText(), "Move closer")

    def test_free_run_without_workout_is_read_only_in_practice(self):
        w, m = self.gym()
        w.run(12)
        m.console_start(1.0)
        w.timer_start()
        m.console_speed(10.0)
        w.run(120)
        self.assertEqual(w.commands(m), [])
        self.assertGreater(w.st.sessDist, 250)


if __name__ == "__main__":
    unittest.main()
