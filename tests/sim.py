"""
A small simulator: a fake Connect IQ Bluetooth stack, fake FTMS treadmills and a fake
watch running a Garmin workout. Drives the ported logic in ftms_logic.py exactly the way
the watch would: BLE callbacks arrive asynchronously, compute() runs once per second.
"""
import ftms_logic as L
from ftms_logic import CLOCK, Ftms, BleException


class Events:
    def __init__(self):
        self.items = []
        self.seq = 0

    def at(self, ms, fn):
        self.seq += 1
        self.items.append((ms, self.seq, fn))

    def after(self, delay, fn):
        self.at(CLOCK.ms + delay, fn)

    def run_due(self):
        while True:
            due = [e for e in self.items if e[0] <= CLOCK.ms]
            if not due:
                return
            due.sort()
            e = due[0]
            self.items.remove(e)
            e[2]()


EV = Events()


# ---------------------------------------------------------------- fake treadmill
class FakeTreadmill:
    def __init__(self, name="MATRIX-12", rssi=-55, target_bits=0x03, speed_range=(80, 2000, 10),
                 incline_range=(0, 150, 5), needs_ftms_start=False, status_on_target=True,
                 indications=True, split_packets=False, has_distance=True, chars=None,
                 control_allowed=True, accel=1.5, incline_rate=0.6, incline_step=0.0,
                 refuse_speed=False, discovery_delay=0, safety_instant=True):
        self.name = name
        self.rssi = rssi
        self.target_bits = target_bits
        self.speed_range = speed_range
        self.incline_range = incline_range
        self.needs_ftms_start = needs_ftms_start
        self.status_on_target = status_on_target
        self.indications = indications
        self.split_packets = split_packets
        self.has_distance = has_distance
        self.control_allowed = control_allowed
        self.accel = accel                  # km/h per second
        self.incline_rate = incline_rate    # % per second
        self.incline_step = incline_step    # >0: console reports incline in steps of this size
        self.refuse_speed = refuse_speed    # answer every speed command with "operation failed"
        self.discovery_delay = discovery_delay  # ms after connecting before characteristics appear
        self.safety_instant = safety_instant    # False: belt coasts down after the key is pulled
        self.connected_at = 0
        self.chars = chars or ["2acc", "2acd", "2ad4", "2ad5", "2ad9", "2ada"]
        self.connected = False
        self.notify = set()
        self.controlled = False
        self.ftms_started = False
        # physics
        self.speed = 0.0
        self.target_speed = 0.0
        self.incline = 0.0
        self.target_incline = 0.0
        self.distance = 0.0
        self.elev = 0.0
        self.safety = False
        self.cp_log = []          # (ms, bytes) of every Control Point write
        self.device = None

    # console actions
    def console_start(self, speed=1.0):
        self.safety = False
        self.target_speed = speed
        self.status(bytes([Ftms.ST_STARTED_BY_USER]))

    def console_stop(self):
        self.target_speed = 0.0
        self.status(bytes([Ftms.ST_STOPPED_BY_USER, 0x01]))

    def console_speed(self, v):
        self.target_speed = v
        if self.status_on_target:
            raw = int(round(v * 100))
            self.status(bytes([Ftms.ST_TARGET_SPEED, raw & 0xFF, raw >> 8]))

    def console_incline(self, v):
        self.target_incline = v
        if self.status_on_target:
            raw = int(round(v * 10)) & 0xFFFF
            self.status(bytes([Ftms.ST_TARGET_INCLINE, raw & 0xFF, raw >> 8]))

    def pull_safety_key(self):
        self.safety = True
        if self.safety_instant:
            self.speed = 0.0
        self.target_speed = 0.0
        self.status(bytes([Ftms.ST_SAFETY_KEY]))

    # BLE side
    def status(self, data):
        if self.connected and "2ada" in self.notify and self.device:
            self.device.notify("2ada", data)

    def on_disconnect(self):
        self.connected = False
        self.notify = set()
        self.controlled = False

    def read(self, uuid):
        if uuid == "2acc":
            return bytes([0x0C, 0x14, 0, 0, self.target_bits, 0, 0, 0])
        if uuid == "2ad4":
            a, b, c = self.speed_range
            return bytes([a & 0xFF, a >> 8, b & 0xFF, b >> 8, c & 0xFF, c >> 8])
        if uuid == "2ad5":
            a, b, c = self.incline_range
            a &= 0xFFFF
            b &= 0xFFFF
            return bytes([a & 0xFF, a >> 8, b & 0xFF, b >> 8, c & 0xFF, c >> 8])
        return None

    def control_point(self, data):
        self.cp_log.append((CLOCK.ms, bytes(data)))
        op = data[0]
        res = Ftms.RES_SUCCESS
        if op == Ftms.CP_REQUEST_CONTROL:
            if self.control_allowed:
                self.controlled = True
            else:
                res = Ftms.RES_NOT_PERMITTED
        elif not self.controlled:
            res = Ftms.RES_NOT_PERMITTED
        elif op == Ftms.CP_SET_SPEED:
            if not (self.target_bits & 1):
                res = Ftms.RES_NOT_SUPPORTED
            elif self.refuse_speed:
                res = Ftms.RES_FAILED
            elif self.needs_ftms_start and not self.ftms_started:
                res = Ftms.RES_FAILED
            else:
                v = Ftms.u16(data, 1) / 100.0
                self.target_speed = v
                if self.status_on_target:
                    self.status(bytes([Ftms.ST_TARGET_SPEED, data[1], data[2]]))
        elif op == Ftms.CP_SET_INCLINE:
            if not (self.target_bits & 2):
                res = Ftms.RES_NOT_SUPPORTED
            elif self.needs_ftms_start and not self.ftms_started:
                res = Ftms.RES_FAILED
            else:
                self.target_incline = Ftms.s16(data, 1) / 10.0
                if self.status_on_target:
                    self.status(bytes([Ftms.ST_TARGET_INCLINE, data[1], data[2]]))
        elif op == Ftms.CP_START_RESUME:
            self.ftms_started = True
            if self.target_speed <= 0.0 and not self.safety:
                self.target_speed = 1.0
        elif op == Ftms.CP_STOP_PAUSE:
            self.target_speed = 0.0
        else:
            res = Ftms.RES_NOT_SUPPORTED
        if self.indications and "2ad9" in self.notify:
            EV.after(80, lambda: self.device and self.connected and
                     self.device.notify("2ad9", bytes([0x80, op, res])))

    def step(self, dt):
        """physics, dt seconds"""
        if self.safety and self.safety_instant:
            self.speed = 0.0
        else:
            dv = self.target_speed - self.speed
            self.speed += max(-self.accel * dt, min(self.accel * dt, dv))
        di = self.target_incline - self.incline
        self.incline += max(-self.incline_rate * dt, min(self.incline_rate * dt, di))
        self.distance += self.speed / 3.6 * dt
        if self.incline > 0:
            self.elev += self.speed / 3.6 * dt * self.incline / 100.0

    def packet(self):
        sp = int(round(self.speed * 100))
        dist = int(self.distance)
        shown = self.incline
        if self.incline_step > 0:
            shown = round(self.incline / self.incline_step) * self.incline_step
        inc = int(round(shown * 10)) & 0xFFFF
        if self.split_packets:
            a = bytes([0x00, 0x00, sp & 0xFF, sp >> 8])
            flags = 0x0001 | 0x0008 | (0x0004 if self.has_distance else 0)
            b = bytes([flags & 0xFF, flags >> 8])
            if self.has_distance:
                b += bytes([dist & 0xFF, (dist >> 8) & 0xFF, dist >> 16])
            b += bytes([inc & 0xFF, inc >> 8, 0, 0])
            return [a, b]
        flags = 0x0008 | 0x0400 | (0x0004 if self.has_distance else 0)
        p = bytes([flags & 0xFF, flags >> 8, sp & 0xFF, sp >> 8])
        if self.has_distance:
            p += bytes([dist & 0xFF, (dist >> 8) & 0xFF, dist >> 16])
        p += bytes([inc & 0xFF, inc >> 8, 0, 0])
        p += bytes([0x10, 0x00])   # elapsed time
        return [p]


# ---------------------------------------------------------------- fake Connect IQ BLE
class ScanResult:
    def __init__(self, machine):
        self.machine = machine
        self.name = machine.name
        self.rssi = machine.rssi
        self.serviceUuids = ["1826"]

    def isSameDevice(self, other):
        return other.machine is self.machine


class FakeDesc:
    def __init__(self, ch):
        self.ch = ch

    def requestWrite(self, value):
        ble = self.ch.dev.ble
        if ble.outstanding:
            raise BleException("busy")
        ble.outstanding = True
        m = self.ch.dev.machine

        def done():
            ble.outstanding = False
            if value[0] in (1, 2):
                m.notify.add(self.ch.uuid)
            ble.delegate.onDescriptorWrite(self, L.STATUS_SUCCESS)
        EV.after(40, done)


class FakeChar:
    def __init__(self, dev, uuid):
        self.dev = dev
        self.uuid = uuid

    def getDescriptor(self, u):
        return FakeDesc(self)

    def requestRead(self):
        ble = self.dev.ble
        if ble.outstanding:
            raise BleException("busy")
        ble.outstanding = True

        def done():
            ble.outstanding = False
            ble.delegate.onCharacteristicRead(self, L.STATUS_SUCCESS, self.dev.machine.read(self.uuid))
        EV.after(40, done)

    def requestWrite(self, value, with_response=True):
        ble = self.dev.ble
        if ble.outstanding:
            raise BleException("busy")
        ble.outstanding = True
        m = self.dev.machine

        def done():
            ble.outstanding = False
            ble.delegate.onCharacteristicWrite(self, L.STATUS_SUCCESS)
            m.control_point(value)
        EV.after(40, done)


class FakeService:
    def __init__(self, dev):
        self.dev = dev

    def getCharacteristic(self, uuid):
        m = self.dev.machine
        if CLOCK.ms < m.connected_at + m.discovery_delay:
            return None                    # service known, characteristics not discovered yet
        if uuid in m.chars and uuid in self.dev.ble.registered:
            return FakeChar(self.dev, uuid)
        return None


class FakeDevice:
    def __init__(self, ble, machine):
        self.ble = ble
        self.machine = machine
        machine.device = self

    def isConnected(self):
        return self.machine.connected

    def getService(self, uuid):
        if self.machine.connected and uuid == "1826":
            return FakeService(self)
        return None

    def notify(self, uuid, data):
        if uuid in self.machine.notify:
            self.ble.delegate.onCharacteristicChanged(FakeChar(self, uuid), data)


class FakeBle:
    def __init__(self, machines, profile_limit=6):
        self.machines = machines
        self.delegate = None
        self.registered = []
        self.scanning = False
        self.paired = []
        self.outstanding = False
        self.profile_limit = profile_limit   # max characteristics this "watch" accepts

    def registerProfile(self, svc, chars):
        ok = len(chars) <= self.profile_limit
        if ok:
            self.registered = list(chars)
        EV.after(100, lambda: self.delegate.onProfileRegister(svc, L.STATUS_SUCCESS if ok else 2))

    def setScanState(self, s):
        self.scanning = (s == L.SCAN_STATE_SCANNING)

    def scan_tick(self):
        if self.scanning:
            self.delegate.onScanResults([ScanResult(m) for m in self.machines])

    def pairDevice(self, result):
        dev = FakeDevice(self, result.machine)
        self.paired.append(dev)

        def connect():
            if dev in self.paired:
                result.machine.connected = True
                result.machine.connected_at = CLOCK.ms
                self.delegate.onConnectedStateChanged(dev, L.CONNECTION_STATE_CONNECTED)
        EV.after(600, connect)
        return dev

    def getPairedDevices(self):
        return list(self.paired)

    def unpairDevice(self, d):
        if d in self.paired:
            self.paired.remove(d)
        d.machine.on_disconnect()
        self.outstanding = False


# ---------------------------------------------------------------- fake watch + workout
class Workout:
    """steps: list of (seconds, WorkoutStepInfo)"""
    def __init__(self, steps):
        self.steps = steps
        self.index = 0
        self.elapsed_in_step = 0.0

    def getCurrentWorkoutStep(self):
        if self.steps is None or self.index >= len(self.steps):
            return None
        return self.steps[self.index][1]


class Watch:
    def __init__(self, machines, workout=None, cfg=None, profile_limit=6):
        CLOCK.ms = 1000
        EV.items.clear()
        self.machines = machines
        self.ble = FakeBle(machines, profile_limit)
        self.cfg = cfg or L.FieldSettings()
        self.st = L.TreadmillState()
        self.mgr = L.FtmsManager(self.st, self.cfg, self.ble)
        self.ble.delegate = self.mgr
        self.workout = workout or Workout(None)
        self.follower = L.WorkoutFollower(self.mgr, self.st, self.cfg, self.workout)
        self.mgr.follower = self.follower
        self.mgr.start()
        self.timer_on = False
        self.last_compute = 0
        self.log = []          # (ms, mode, status, target speed, belt speed)
        self.step_changes = []

    def timer_start(self):
        self.timer_on = True
        self.follower.onTimerStart()

    def timer_pause(self):
        self.timer_on = False
        self.follower.onTimerPause()

    def timer_resume(self):
        self.timer_on = True
        self.follower.onTimerResume()

    def lap(self):
        """lap key: in a workout it ends the step (onTimerLap + onWorkoutStepComplete)"""
        w = self.workout
        self.st.lapReset()
        if w.steps is not None and w.index < len(w.steps):
            w.index += 1
            w.elapsed_in_step = 0.0
            self.follower.onStepComplete()

    def auto_lap(self):
        """Auto Lap (every km): only onTimerLap, the workout step does not change"""
        self.st.lapReset()

    def run(self, seconds, every=None):
        end = CLOCK.ms + int(seconds * 1000)
        while CLOCK.ms < end:
            CLOCK.ms += 100
            for m in self.machines:
                m.step(0.1)
            EV.run_due()
            if CLOCK.ms % 1000 == 0:
                for m in self.machines:
                    if m.connected and "2acd" in m.notify and m.device:
                        for p in m.packet():
                            m.device.notify("2acd", p)
                self.ble.scan_tick()
                EV.run_due()
                self.compute()
                if every:
                    every(self)

    def compute(self):
        now = CLOCK.ms
        dt = 0.0 if self.last_compute == 0 else (now - self.last_compute) / 1000.0
        self.last_compute = now
        # workout progression (time-based steps advance only while the timer runs)
        w = self.workout
        if self.timer_on and w.steps is not None and w.index < len(w.steps):
            w.elapsed_in_step += dt
            if w.elapsed_in_step >= w.steps[w.index][0]:
                w.index += 1
                w.elapsed_in_step = 0.0
                self.st.lapReset()                 # view.onTimerLap
                self.follower.onStepComplete()     # view.onWorkoutStepComplete
                self.step_changes.append(now)
        self.mgr.tick()
        self.st.tick(self.timer_on, dt)
        self.follower.tick(self.timer_on)
        self.log.append((now, self.follower.modeText(), self.mgr.statusText(),
                         self.follower.targetSpeed, round(self.st.speedKmh, 2)))

    def commands(self, machine=None):
        m = machine or self.machines[0]
        out = []
        for ms, b in m.cp_log:
            op = b[0]
            if op == 0x02:
                out.append(("speed", Ftms.u16(b, 1) / 100.0))
            elif op == 0x03:
                out.append(("incline", Ftms.s16(b, 1) / 10.0))
            elif op == 0x00:
                out.append(("control",))
            elif op == 0x07:
                out.append(("start",))
            elif op == 0x08:
                out.append(("pause",))
            else:
                out.append(("op%02x" % op,))
        return out


def pace_mps(pace_str):
    m, s = pace_str.split(":")
    return 1000.0 / (int(m) * 60 + int(s))


def step(name, notes, intensity, secs, pace_fast=None, pace_slow=None):
    if pace_fast:
        st = L.WorkoutStep(0, pace_mps(pace_slow), pace_mps(pace_fast), 0, secs * 1000)
    else:
        st = L.WorkoutStep(2, 0, 0, 0, secs * 1000)
    return (secs, L.WorkoutStepInfo(name, notes, intensity, st))


def interval_workout(with_notes=True):
    """15 min warm-up, 3 x (9 min run + 2 min recovery), 15 min cool-down, with pace targets
    and, optionally, the speed and incline written in the step notes."""
    wu = "Treadmill 12.0 km/h, 1% incline" if with_notes else None
    run = "Treadmill 13.3 km/h, 1% incline. Threshold pace (2 km)" if with_notes else None
    rec = "Treadmill 10.0 km/h, 1% incline. Easy jog" if with_notes else None
    steps = [step("Warm Up", wu, 2, 15 * 60, "4:55", "5:05")]
    for _ in range(3):
        steps.append(step("Run", run, 0, 9 * 60, "4:28", "4:34"))
        steps.append(step("Recover", rec, 4, 2 * 60, "5:50", "6:10"))
    steps.append(step("Cool Down", None, 3, 15 * 60, "4:55", "5:05"))
    return Workout(steps)
