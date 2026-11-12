"""
Python port of the data field's logic, kept line-for-line close to the Monkey C so it
can be tested without a Garmin watch or the Connect IQ SDK:

    Ftms.mc            -> class Ftms
    Targets.mc         -> class Targets, class Fmt
    TreadmillState.mc  -> class TreadmillState
    FtmsManager.mc     -> class FtmsManager   (Toybox.BluetoothLowEnergy calls go to `ble`)
    WorkoutFollower.mc -> class WorkoutFollower (Activity.getCurrentWorkoutStep goes to `activity`)

If you change one side, change the other.
"""
import math


def mc_round(x):
    """Monkey C Math.round for the values used here (rounds .5 away from zero)."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


class Clock:
    """System.getTimer() stand-in (ms)."""
    def __init__(self):
        self.ms = 1000

    def now(self):
        return self.ms


CLOCK = Clock()


def get_timer():
    return CLOCK.now()


# ---------------------------------------------------------------- Ftms.mc
class Ftms:
    F_MORE_DATA = 0x0001
    F_AVG_SPEED = 0x0002
    F_TOTAL_DISTANCE = 0x0004
    F_INCLINATION = 0x0008
    F_ELEVATION = 0x0010
    F_INST_PACE = 0x0020
    F_AVG_PACE = 0x0040
    F_ENERGY = 0x0080
    F_HEART_RATE = 0x0100
    F_MET = 0x0200
    F_ELAPSED = 0x0400
    F_REMAINING = 0x0800
    F_FORCE_POWER = 0x1000

    HAS_SPEED = 1
    HAS_DISTANCE = 2
    HAS_INCLINE = 4
    HAS_ELEVATION = 8

    CP_REQUEST_CONTROL = 0x00
    CP_RESET = 0x01
    CP_SET_SPEED = 0x02
    CP_SET_INCLINE = 0x03
    CP_START_RESUME = 0x07
    CP_STOP_PAUSE = 0x08
    CP_RESPONSE = 0x80

    RES_SUCCESS = 0x01
    RES_NOT_SUPPORTED = 0x02
    RES_INVALID_PARAM = 0x03
    RES_FAILED = 0x04
    RES_NOT_PERMITTED = 0x05

    ST_RESET = 0x01
    ST_STOPPED_BY_USER = 0x02
    ST_SAFETY_KEY = 0x03
    ST_STARTED_BY_USER = 0x04
    ST_TARGET_SPEED = 0x05
    ST_TARGET_INCLINE = 0x06
    ST_CONTROL_LOST = 0xFF

    MPH_TO_KMH = 1.609344

    @staticmethod
    def u16(b, i):
        return b[i] | (b[i + 1] << 8)

    @staticmethod
    def s16(b, i):
        v = b[i] | (b[i + 1] << 8)
        if v >= 0x8000:
            v = v - 0x10000
        return v

    @staticmethod
    def u24(b, i):
        return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16)

    @staticmethod
    def u32(b, i):
        return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)

    @staticmethod
    def parseTreadmillData(b, st, mph):
        n = len(b)
        if n < 2:
            return -1
        flags = Ftms.u16(b, 0)
        i = 2
        got = 0
        scale = Ftms.MPH_TO_KMH if mph else 1.0
        if (flags & Ftms.F_MORE_DATA) == 0:
            if i + 2 > n: return got
            st.setSpeed(Ftms.u16(b, i) / 100.0 * scale)
            got |= Ftms.HAS_SPEED
            i += 2
        if (flags & Ftms.F_AVG_SPEED) != 0:
            if i + 2 > n: return got
            i += 2
        if (flags & Ftms.F_TOTAL_DISTANCE) != 0:
            if i + 3 > n: return got
            st.onMachineDistance(Ftms.u24(b, i))
            got |= Ftms.HAS_DISTANCE
            i += 3
        if (flags & Ftms.F_INCLINATION) != 0:
            if i + 4 > n: return got
            raw = Ftms.s16(b, i)
            if raw != 0x7FFF:
                st.setIncline(raw / 10.0)
                got |= Ftms.HAS_INCLINE
            i += 4
        if (flags & Ftms.F_ELEVATION) != 0:
            if i + 4 > n: return got
            st.onMachineElevation(Ftms.u16(b, i) / 10.0)
            got |= Ftms.HAS_ELEVATION
            i += 4
        if (flags & Ftms.F_INST_PACE) != 0: i += 1
        if (flags & Ftms.F_AVG_PACE) != 0: i += 1
        if (flags & Ftms.F_ENERGY) != 0:
            if i + 5 > n: return got
            kcal = Ftms.u16(b, i)
            if kcal != 0xFFFF:
                st.machineEnergy = kcal
            i += 5
        if (flags & Ftms.F_HEART_RATE) != 0:
            if i + 1 > n: return got
            st.machineHr = b[i]
            i += 1
        if (flags & Ftms.F_MET) != 0: i += 1
        if (flags & Ftms.F_ELAPSED) != 0:
            if i + 2 > n: return got
            st.machineElapsed = Ftms.u16(b, i)
            i += 2
        return got

    @staticmethod
    def parseFeature(b):
        if len(b) < 8:
            return None
        return [Ftms.u32(b, 0), Ftms.u32(b, 4)]

    @staticmethod
    def parseSpeedRange(b, st, mph):
        if len(b) < 6:
            return False
        scale = Ftms.MPH_TO_KMH if mph else 1.0
        mn = Ftms.u16(b, 0) / 100.0 * scale
        mx = Ftms.u16(b, 2) / 100.0 * scale
        inc = Ftms.u16(b, 4) / 100.0 * scale
        if mx <= mn or mx <= 0.0:
            return False
        st.minSpeed = mn
        st.maxSpeed = mx
        if 0.0 < inc <= 1.0:
            st.speedInc = inc
        return True

    @staticmethod
    def parseInclineRange(b, st):
        if len(b) < 6:
            return False
        mn = Ftms.s16(b, 0) / 10.0
        mx = Ftms.s16(b, 2) / 10.0
        inc = Ftms.u16(b, 4) / 10.0
        if mx <= mn:
            return False
        st.minIncline = mn
        st.maxIncline = mx
        if 0.0 < inc <= 5.0:
            st.inclineInc = inc
        return True

    @staticmethod
    def encodeSpeed(kmh, mph):
        v = kmh / Ftms.MPH_TO_KMH if mph else kmh
        raw = int(mc_round(v * 100.0))
        if raw < 0:
            raw = 0
        return bytes([Ftms.CP_SET_SPEED, raw & 0xFF, (raw >> 8) & 0xFF])

    @staticmethod
    def encodeIncline(pct):
        raw = int(mc_round(pct * 10.0))
        if raw < 0:
            raw = raw + 0x10000
        return bytes([Ftms.CP_SET_INCLINE, raw & 0xFF, (raw >> 8) & 0xFF])

    @staticmethod
    def rawHasFtms(raw):
        if raw is None:
            return False
        n = len(raw)
        i = 0
        while i < n:
            ln = raw[i]
            if ln == 0 or i + ln >= n:
                break
            t = raw[i + 1]
            if t in (0x02, 0x03):
                j = i + 2
                while j + 1 <= i + ln:
                    if raw[j] == 0x26 and raw[j + 1] == 0x18:
                        return True
                    j += 2
            elif t == 0x16:
                if ln >= 3 and raw[i + 2] == 0x26 and raw[i + 3] == 0x18:
                    return True
            elif t in (0x06, 0x07):
                k = i + 2
                while k + 15 <= i + ln:
                    if raw[k] == 0xFB and raw[k + 12] == 0x26 and raw[k + 13] == 0x18 \
                            and raw[k + 14] == 0 and raw[k + 15] == 0:
                        return True
                    k += 16
            i += ln + 1
        return False

    @staticmethod
    def resultText(code):
        return {1: "OK", 2: "Not supported", 3: "Bad value", 4: "Refused", 5: "No control",
                -2: "Write failed"}.get(code, "No reply")


# ---------------------------------------------------------------- Targets.mc
class Targets:
    @staticmethod
    def speedFromText(text):
        if text is None:
            return None
        t = text.lower()
        v = Targets.numberBefore(t, "km/h")
        if v is None: v = Targets.numberBefore(t, "kph")
        if v is None: v = Targets.numberBefore(t, "kmh")
        if v is None:
            m = Targets.numberBefore(t, "mph")
            if m is not None:
                v = m * 1.609344
        if v is not None and (v < 0.5 or v > 30.0):
            return None
        return v

    @staticmethod
    def inclineFromText(text):
        if text is None:
            return None
        v = Targets.numberBefore(text.lower(), "%")
        if v is not None and (v < -10.0 or v > 40.0):
            return None
        return v

    @staticmethod
    def numberBefore(t, unit):
        idx = t.find(unit)
        if idx < 0:
            return None
        chars = t
        end = idx
        while end > 0 and ord(chars[end - 1]) == 32:
            end -= 1
        start = end
        while start > 0:
            ch = ord(chars[start - 1])
            if (48 <= ch <= 57) or ch == 46 or ch == 44:
                start -= 1
            else:
                break
        while start < end and not Targets.isDigit(ord(chars[start])):
            start += 1
        while end > start and not Targets.isDigit(ord(chars[end - 1])):
            end -= 1
        if start >= end:
            return None
        s = "".join("." if ord(c) == 44 else c for c in chars[start:end])
        try:
            v = float(s)            # Monkey C String.toFloat() returns null on garbage
        except ValueError:
            return None
        if start > 0 and ord(chars[start - 1]) == 45:
            v = -v
        return v

    @staticmethod
    def isDigit(c):
        return 48 <= c <= 57

    @staticmethod
    def speedFromStep(info):
        s = info.step
        if s is None:
            return None
        if isinstance(s, WorkoutIntervalStep):
            it = info.intensity
            s = s.restStep if it in (1, 4) else s.activeStep
            if s is None:
                return None
        if not isinstance(s, WorkoutStep):
            return None
        return Targets.speedFromTarget(s.targetType, s.targetValueLow, s.targetValueHigh)

    @staticmethod
    def speedFromTarget(targetType, lo, hi):
        if targetType != 0:
            return None
        a = Targets.toMps(lo)
        b = Targets.toMps(hi)
        if a <= 0.0 or b <= 0.0:
            return None
        if a == b and a <= 10.0 and a == float(int(a)):
            return None
        mps = (a + b) / 2.0
        if mps < 0.3 or mps > 8.5:
            return None
        return mps * 3.6

    @staticmethod
    def toMps(v):
        if v is None:
            return 0.0
        f = float(v)
        if f > 50.0:
            f = f / 1000.0
        return f

    @staticmethod
    def roundTo(v, inc):
        if inc <= 0.0:
            return v
        return mc_round(v / inc) * inc

    @staticmethod
    def clampSpeed(v, st, userMax):
        mx = st.maxSpeed if st.maxSpeed < userMax else userMax
        r = Targets.roundTo(Targets.clamp(v, st.minSpeed, mx), st.speedInc)
        if r > mx + 0.001:
            r -= st.speedInc
        return r

    @staticmethod
    def clampIncline(v, st, userMax):
        mx = st.maxIncline if st.maxIncline < userMax else userMax
        r = Targets.roundTo(Targets.clamp(v, st.minIncline, mx), st.inclineInc)
        if r > mx + 0.001:
            r -= st.inclineInc
        if r < st.minIncline - 0.001:
            r += st.inclineInc
        return r

    @staticmethod
    def clamp(v, lo, hi):
        if v < lo: return lo
        if v > hi: return hi
        return v


class Fmt:
    @staticmethod
    def pace(kmh):
        if kmh is None or kmh < 0.5:
            return "--:--"
        secs = int(mc_round(3600.0 / kmh))
        return "%d:%02d" % (secs // 60, secs % 60)

    @staticmethod
    def one(v):
        return "%.1f" % v


# ---------------------------------------------------------------- Activity stand-ins
class WorkoutStep:
    def __init__(self, targetType=2, lo=0, hi=0, durationType=0, durationValue=0):
        self.targetType = targetType
        self.targetValueLow = lo
        self.targetValueHigh = hi
        self.durationType = durationType
        self.durationValue = durationValue


class WorkoutIntervalStep:
    def __init__(self, activeStep, restStep):
        self.activeStep = activeStep
        self.restStep = restStep


class WorkoutStepInfo:
    def __init__(self, name, notes, intensity, step):
        self.name = name
        self.notes = notes
        self.intensity = intensity
        self.step = step


def mc_str(v):
    """Monkey C string concatenation of null gives "null"."""
    return "null" if v is None else str(v)


# ---------------------------------------------------------------- TreadmillState.mc
class TreadmillState:
    def __init__(self):
        self.hasSpeed = False
        self.speedKmh = 0.0
        self.hasIncline = False
        self.incline = 0.0
        self.hasMachineDistance = False
        self.hasMachineElevation = False
        self.machineEnergy = -1
        self.machineHr = 0
        self.machineElapsed = -1
        self.machineTargetSpeed = -1.0
        self.machineTargetIncline = -100.0
        self.lastPacketMs = 0
        self.stoppedByUser = False
        self.safetyKey = False
        self.minSpeed = 0.8
        self.maxSpeed = 20.0
        self.speedInc = 0.1
        self.minIncline = 0.0
        self.maxIncline = 15.0
        self.inclineInc = 0.5
        self.timerOn = False
        self.sessDist = 0.0
        self.sessTime = 0.0
        self.sessAscent = 0.0
        self.sessInclineDist = 0.0
        self.lapDist = 0.0
        self.lapTime = 0.0
        self.lapAscent = 0.0
        self.lapInclineDist = 0.0
        self._distBase = -1
        self._elevBase = -1.0

    def isFresh(self):
        return self.lastPacketMs != 0 and get_timer() - self.lastPacketMs < 5000

    def beltRunning(self):
        return self.isFresh() and self.hasSpeed and self.speedKmh > 0.3 and not self.safetyKey

    def setSpeed(self, v):
        gap = self.lastPacketMs != 0 and get_timer() - self.lastPacketMs > 5000
        if v > 0.3 and (not self.hasSpeed or self.speedKmh <= 0.3 or gap):
            self.stoppedByUser = False
            self.safetyKey = False
        self.hasSpeed = True
        self.speedKmh = v
        self.lastPacketMs = get_timer()

    def setIncline(self, v):
        self.hasIncline = True
        self.incline = v
        self.lastPacketMs = get_timer()

    def onMachineDistance(self, total):
        self.hasMachineDistance = True
        self.lastPacketMs = get_timer()
        if self._distBase < 0:
            self._distBase = total
            return
        d = total - self._distBase
        self._distBase = total
        if 0 < d < 1000 and self.timerOn:
            self._addDistance(float(d))

    def onMachineElevation(self, total):
        self.hasMachineElevation = True
        if self._elevBase < 0.0:
            self._elevBase = total
            return
        d = total - self._elevBase
        self._elevBase = total
        if 0.0 < d < 100.0 and self.timerOn:
            self.sessAscent += d
            self.lapAscent += d

    def tick(self, isTimerOn, dt):
        self.timerOn = isTimerOn
        if not self.timerOn or not self.beltRunning() or dt <= 0.0:
            return
        self.sessTime += dt
        self.lapTime += dt
        if not self.hasMachineDistance:
            self._addDistance(self.speedKmh / 3.6 * dt)

    def _addDistance(self, d):
        self.sessDist += d
        self.lapDist += d
        inc = self.incline if self.hasIncline else 0.0
        self.sessInclineDist += inc * d
        self.lapInclineDist += inc * d
        if not self.hasMachineElevation and inc > 0.0:
            up = d * inc / 100.0
            self.sessAscent += up
            self.lapAscent += up

    def sessAvgSpeed(self):
        return self.sessDist / self.sessTime * 3.6 if self.sessTime > 0.0 else 0.0

    def lapAvgSpeed(self):
        return self.lapDist / self.lapTime * 3.6 if self.lapTime > 0.0 else 0.0

    def sessAvgIncline(self):
        return self.sessInclineDist / self.sessDist if self.sessDist > 0.0 else 0.0

    def lapAvgIncline(self):
        return self.lapInclineDist / self.lapDist if self.lapDist > 0.0 else 0.0

    def lapReset(self):
        self.lapDist = self.lapTime = self.lapAscent = self.lapInclineDist = 0.0

    def sessionReset(self):
        self.lapReset()
        self.sessDist = self.sessTime = self.sessAscent = self.sessInclineDist = 0.0


# ---------------------------------------------------------------- FieldSettings.mc
class FieldSettings:
    def __init__(self, **kw):
        self.nameFilter = ""
        self.rssiMin = -90
        self.controlEnabled = True
        self.useNotes = True
        self.usePaceTarget = True
        self.defaultIncline = 1.0
        self.maxSpeed = 16.0
        self.maxIncline = 8.0
        self.autoResume = True
        self.remoteStart = False
        self.pauseWithWatch = False
        self.mphMode = False
        self.recordFit = True
        self.mainValue = 0
        for k, v in kw.items():
            assert hasattr(self, k), k
            setattr(self, k, v)


# ---------------------------------------------------------------- FtmsManager.mc
SCAN_STATE_OFF, SCAN_STATE_SCANNING = 0, 1
STATUS_SUCCESS = 0
CONNECTION_STATE_CONNECTED, CONNECTION_STATE_DISCONNECTED = 1, 2
CCCD = "2902"

LINK_REGISTERING, LINK_SCANNING, LINK_CONNECTING, LINK_DISCOVERING, LINK_SETUP, LINK_READY, \
    LINK_RECONNECTING, LINK_ERROR = range(8)
OP_READ, OP_CCCD, OP_CP = range(3)


class BleException(Exception):
    pass


class FtmsManager:
    def __init__(self, st, cfg, ble):
        self.ble = ble
        self.link = LINK_REGISTERING
        self.deviceName = ""
        self.problem = ""
        self.follower = None
        self.profileLevel = 0
        self.machineFeatures = 0
        self.targetFeatures = 0
        self.speedSupported = True
        self.inclineSupported = True
        self.hasControl = False
        self.controlDenied = False
        self.st = st
        self.cfg = cfg
        self.svcUuid = "1826"
        self.featUuid = "2acc"
        self.dataUuid = "2acd"
        self.speedRangeUuid = "2ad4"
        self.inclRangeUuid = "2ad5"
        self.cpUuid = "2ad9"
        self.statusUuid = "2ada"
        self.device = None
        self.linkSince = 0
        self.scanStart = 0
        self.scanning = False
        self.candidates = []     # [scanResult, rssi, name, proven]
        self.seen = []           # [scanResult, rssi, name, ms]
        self.excluded = []
        self.target = None
        self.targetProven = False
        self.searchSince = 0
        self.queue = []
        self.busy = False
        self.busyOp = None
        self.busySince = 0
        self.cpWaiting = -1
        self.cpSince = 0
        self.noIndications = False
        self.controlRequested = False
        self.notPermittedRetries = 0
        self.resumeTried = False
        self.lastSpeedCmd = None
        self.lastInclineCmd = None

    # public
    def start(self):
        self.setLink(LINK_REGISTERING)
        self.registerProfile(3)

    def shutdown(self):
        self.setScan(False)
        self.unpairAll()
        self.device = None

    def isReady(self):
        return self.link == LINK_READY

    def controllable(self):
        return self.profileLevel >= 2 and (self.speedSupported or self.inclineSupported) \
            and self.getChar(self.cpUuid) is not None

    def canControl(self):
        return self.link == LINK_READY and self.cfg.controlEnabled and not self.controlDenied \
            and self.controllable()

    def clearControlDenied(self):
        self.controlDenied = False
        self.notPermittedRetries = 0
        self.resumeTried = False

    def requestSpeed(self, kmh):
        if not self.canControl() or not self.speedSupported:
            return False
        self.lastSpeedCmd = kmh
        self.ensureControl()
        self.enqueueCp(Ftms.encodeSpeed(kmh, self.cfg.mphMode), True)
        self.pump()
        return True

    def requestIncline(self, pct):
        if not self.canControl() or not self.inclineSupported:
            return False
        self.lastInclineCmd = pct
        self.ensureControl()
        self.enqueueCp(Ftms.encodeIncline(pct), True)
        self.pump()
        return True

    def requestStartResume(self):
        if not self.canControl():
            return False
        self.ensureControl()
        self.enqueueCp(bytes([0x07]), True)
        self.pump()
        return True

    def requestPause(self):
        if not self.canControl():
            return False
        self.ensureControl()
        self.enqueueCp(bytes([0x08, 0x02]), True)
        self.pump()
        return True

    def statusText(self):
        if self.link == LINK_REGISTERING: return "Starting BLE"
        if self.link == LINK_ERROR: return self.problem
        if self.link == LINK_SCANNING:
            if len(self.candidates) == 0:
                if get_timer() - self.searchSince < 10000: return "Searching..."
                n = len(self.recentSeen())
                return "No BLE devices" if n == 0 else "No FTMS (%d seen)" % n
            if len(self.problem) > 0: return self.problem
            return "Found %d" % len(self.candidates)
        if self.link == LINK_CONNECTING: return "Connecting"
        if self.link in (LINK_DISCOVERING, LINK_SETUP): return "Setting up"
        if self.link == LINK_RECONNECTING: return "Reconnecting"
        return self.deviceName if len(self.deviceName) > 0 else "Treadmill"

    def tick(self):
        now = get_timer()
        if self.link == LINK_REGISTERING:
            if now - self.linkSince > 6000:
                self.startScan()
        elif self.link == LINK_SCANNING:
            if len(self.candidates) > 0 and now - self.scanStart > 4000:
                self.connectBest()
            elif now - self.scanStart > 30000:
                self.setScan(False)
                self.startScan()
        elif self.link == LINK_CONNECTING:
            if self.device is not None and self.device.isConnected():
                self.onLinkUp()
            elif now - self.linkSince > 15000:
                self.dropAndRescan("Connect timeout")
        elif self.link == LINK_DISCOVERING:
            self.discover(now)
        elif self.link in (LINK_SETUP, LINK_READY):
            if self.device is None or not self.device.isConnected():
                self.onLinkDown()
            else:
                self.pump()
        elif self.link == LINK_RECONNECTING:
            if self.device is not None and self.device.isConnected():
                self.onLinkUp()
            elif now - self.linkSince > 10000:
                self.dropAndRescan("Lost link")

    # link
    def setLink(self, l):
        self.link = l
        self.linkSince = get_timer()

    def registerProfile(self, level):
        self.profileLevel = level
        self.linkSince = get_timer()
        if level == 3:
            chars = [self.featUuid, self.dataUuid, self.speedRangeUuid, self.inclRangeUuid, self.cpUuid, self.statusUuid]
        elif level == 2:
            chars = [self.dataUuid, self.cpUuid, self.statusUuid]
        else:
            chars = [self.dataUuid]
        try:
            self.ble.registerProfile(self.svcUuid, chars)
        except BleException:
            self.profileFailed()

    def profileFailed(self):
        if self.profileLevel > 1:
            self.registerProfile(self.profileLevel - 1)
        else:
            self.profileLevel = 0
            self.problem = "BLE busy"
            self.setLink(LINK_ERROR)

    def startScan(self):
        if self.link != LINK_SCANNING:
            self.searchSince = get_timer()
        self.candidates = []
        self.scanStart = get_timer()
        self.device = None
        self.setLink(LINK_SCANNING)
        self.setScan(True)

    def setScan(self, on):
        if on == self.scanning:
            return
        self.ble.setScanState(SCAN_STATE_SCANNING if on else SCAN_STATE_OFF)
        self.scanning = on

    def advertisesFtms(self, r):
        if self.svcUuid in r.serviceUuids:
            return True
        sd = r.serviceData.get(self.svcUuid)
        if sd is not None and len(sd) > 0:
            return True
        return Ftms.rawHasFtms(r.rawData)

    HINTS = ["tread", "matrix", "ftms", "jht", "horizon", "technogym", "precor", "life fitness",
             "lifefitness", "woodway", "nordictrack", "proform", "sole ", "kingsmith", "walkingpad", "echelon"]

    def looksLikeTreadmill(self, lname):
        if lname.find("ctm") == 0:
            return True
        return any(lname.find(h) >= 0 for h in self.HINTS)

    def isExcluded(self, r):
        return any(r.isSameDevice(x) for x in self.excluded)

    def exclude(self, r):
        if r is not None and not self.isExcluded(r) and len(self.excluded) < 8:
            self.excluded.append(r)

    def noteSeen(self, r, name):
        now = get_timer()
        oldest = 0
        for i, e in enumerate(self.seen):
            if r.isSameDevice(e[0]):
                self.seen[i] = [r, r.rssi, name, now]
                return
            if e[3] < self.seen[oldest][3]:
                oldest = i
        if len(self.seen) < 16:
            self.seen.append([r, r.rssi, name, now])
        else:
            self.seen[oldest] = [r, r.rssi, name, now]

    def recentSeen(self):
        now = get_timer()
        return [e for e in self.seen if now - e[3] < 20000]

    def diagText(self):
        if self.link != LINK_SCANNING:
            return ""
        lst = self.recentSeen()
        a = b = -1
        for i, e in enumerate(lst):
            if len(e[2]) == 0:
                continue
            if a < 0 or e[1] > lst[a][1]:
                b = a
                a = i
            elif b < 0 or e[1] > lst[b][1]:
                b = i
        if a < 0:
            return ("%d unnamed" % len(lst)) if len(lst) > 0 else ""
        s = self.shortName(lst[a][2]) + " " + str(lst[a][1])
        if b >= 0:
            s += "  " + self.shortName(lst[b][2]) + " " + str(lst[b][1])
        return s

    @staticmethod
    def shortName(n):
        return n[0:10] if len(n) > 10 else n

    def connectBest(self):
        best, bestRssi, bestName, bestProven = None, -999, "", False
        for c in self.candidates:
            if (c[3] and not bestProven) or (c[3] == bestProven and c[1] > bestRssi):
                best, bestRssi, bestName, bestProven = c
        if best is None:
            return
        if not bestProven and get_timer() - self.scanStart < 8000:
            return
        if bestRssi < self.cfg.rssiMin:
            self.problem = "Move closer"
            return
        self.setScan(False)
        self.unpairAll()
        try:
            self.device = self.ble.pairDevice(best)
        except BleException:
            self.device = None
        if self.device is None:
            self.problem = "Pair failed"
            self.startScan()
            return
        self.problem = ""
        self.deviceName = bestName
        self.target = best
        self.targetProven = bestProven
        self.setLink(LINK_CONNECTING)
        if self.device.isConnected():
            self.onLinkUp()

    def onLinkUp(self):
        self.resetSession()
        self.setLink(LINK_DISCOVERING)
        self.discover(get_timer())

    def onLinkDown(self):
        self.resetSession()
        self.setLink(LINK_RECONNECTING)

    def dropAndRescan(self, reason):
        self.problem = reason
        self.resetSession()
        self.unpairAll()
        self.startScan()

    def discover(self, now):
        svc = self.device.getService(self.svcUuid) if self.device is not None else None
        dataChar = svc.getCharacteristic(self.dataUuid) if svc is not None else None
        if dataChar is not None:
            self.buildSetupQueue()
            self.setLink(LINK_SETUP)
            self.pump()
        elif now - self.linkSince > 10000:
            if not self.targetProven:
                self.exclude(self.target)
            self.dropAndRescan("No FTMS")

    def resetSession(self):
        self.queue = []
        self.busy = False
        self.busyOp = None
        self.cpWaiting = -1
        self.hasControl = False
        self.controlRequested = False
        self.notPermittedRetries = 0
        self.resumeTried = False

    def unpairAll(self):
        for d in list(self.ble.getPairedDevices()):
            self.ble.unpairDevice(d)

    def buildSetupQueue(self):
        self.queue = []
        self.noIndications = False
        if self.profileLevel == 3:
            self.queue.append([OP_READ, self.featUuid, None, 0])
            self.queue.append([OP_READ, self.speedRangeUuid, None, 0])
            self.queue.append([OP_READ, self.inclRangeUuid, None, 0])
        self.queue.append([OP_CCCD, self.dataUuid, bytes([0x01, 0x00]), 0])
        if self.profileLevel >= 2:
            self.queue.append([OP_CCCD, self.statusUuid, bytes([0x01, 0x00]), 0])
            if self.cfg.controlEnabled:
                self.queue.append([OP_CCCD, self.cpUuid, bytes([0x02, 0x00]), 0])

    # GATT queue
    def getChar(self, uuid):
        if self.device is None:
            return None
        svc = self.device.getService(self.svcUuid)
        if svc is None:
            return None
        return svc.getCharacteristic(uuid)

    def ensureControl(self):
        if self.hasControl or self.controlRequested:
            return
        self.controlRequested = True
        self.queue = [[OP_CP, self.cpUuid, bytes([0x00]), 0]] + self.queue

    def enqueueCp(self, data, coalesce):
        if coalesce:
            for op in self.queue:
                if op[0] == OP_CP and op[2][0] == data[0]:
                    op[2] = data
                    return
        self.queue.append([OP_CP, self.cpUuid, data, 0])

    def enqueueCpFirst(self, data):
        at = 0
        if len(self.queue) > 0 and self.queue[0][0] == OP_CP and self.queue[0][2][0] == Ftms.CP_REQUEST_CONTROL:
            at = 1
        self.queue = self.queue[0:at] + [[OP_CP, self.cpUuid, data, 0]] + self.queue[at:]

    def purgeCommands(self):
        self.queue = [op for op in self.queue if op[0] != OP_CP]

    def pump(self):
        now = get_timer()
        if self.busy:
            if now - self.busySince < 5000:
                return
            self.busy = False
            self.busyOp = None
        if self.cpWaiting >= 0:
            if now - self.cpSince < 4000:
                return
            op = self.cpWaiting
            self.cpWaiting = -1
            self.noIndications = True
            self.onCpResult(op, -1)
        while len(self.queue) > 0:
            nxt = self.queue[0]
            self.queue = self.queue[1:]
            r = self.startOp(nxt)
            if r == 1 or r == 2:
                return
        if self.link == LINK_SETUP:
            self.problem = ""
            self.setLink(LINK_READY)
            if self.follower is not None:
                self.follower.onLinkReady()

    def startOp(self, op):
        ch = self.getChar(op[1])
        if ch is None:
            return 0
        try:
            if op[0] == OP_READ:
                ch.requestRead()
            elif op[0] == OP_CCCD:
                d = ch.getDescriptor(CCCD)
                if d is None:
                    return 0
                d.requestWrite(op[2])
            else:
                ch.requestWrite(op[2], with_response=True)
                if not self.noIndications:
                    self.cpWaiting = op[2][0]
                    self.cpSince = get_timer()
        except BleException:
            self.cpWaiting = -1
            op[3] = op[3] + 1
            if op[3] < 3:
                self.queue = [op] + self.queue
                return 2
            if op[0] == OP_CP:
                self.onCpResult(op[2][0], -2)
            return 0
        self.busy = True
        self.busyOp = op
        self.busySince = get_timer()
        return 1

    # control point
    def onCpResult(self, op, result):
        if op == self.cpWaiting:
            self.cpWaiting = -1
        if op == Ftms.CP_REQUEST_CONTROL:
            self.controlRequested = False
            if result == Ftms.RES_SUCCESS or result == -1:
                self.hasControl = True
            else:
                self.hasControl = False
                self.controlDenied = True
                self.problem = "Control write failed" if result == -2 else "Control refused"
                self.purgeCommands()
            return
        if op != Ftms.CP_SET_SPEED and op != Ftms.CP_SET_INCLINE:
            return
        isSpeed = op == Ftms.CP_SET_SPEED
        retrying = False
        if result == Ftms.RES_SUCCESS:
            self.notPermittedRetries = 0
            self.problem = ""
        elif result == Ftms.RES_NOT_SUPPORTED:
            if isSpeed:
                self.speedSupported = False
            else:
                self.inclineSupported = False
            self.problem = ("Speed " if isSpeed else "Incline ") + "not supported"
        elif result == Ftms.RES_NOT_PERMITTED and self.notPermittedRetries < 1:
            self.notPermittedRetries += 1
            self.hasControl = False
            self.controlRequested = False
            self.resend(isSpeed)
            retrying = True
        elif result == Ftms.RES_FAILED and not self.resumeTried and self.st.beltRunning():
            self.resumeTried = True
            self.ensureControl()
            self.enqueueCpFirst(bytes([0x07]))
            self.resend(isSpeed)
            retrying = True
        elif result != -1:
            self.problem = ("Speed " if isSpeed else "Incline ") + Ftms.resultText(result)
        if self.follower is not None and not retrying:
            self.follower.onCommandResult(isSpeed, result)

    def resend(self, isSpeed):
        self.ensureControl()
        if isSpeed and self.lastSpeedCmd is not None:
            self.enqueueCp(Ftms.encodeSpeed(self.lastSpeedCmd, self.cfg.mphMode), True)
        elif not isSpeed and self.lastInclineCmd is not None:
            self.enqueueCp(Ftms.encodeIncline(self.lastInclineCmd), True)

    def onStatus(self, v):
        if len(v) < 1:
            return
        op = v[0]
        if op == Ftms.ST_RESET:
            self.hasControl = False
            self.controlRequested = False
        elif op == Ftms.ST_STOPPED_BY_USER:
            self.st.stoppedByUser = True
        elif op == Ftms.ST_SAFETY_KEY:
            self.st.safetyKey = True
            self.st.stoppedByUser = True
        elif op == Ftms.ST_STARTED_BY_USER:
            self.st.stoppedByUser = False
            self.st.safetyKey = False
        elif op == Ftms.ST_TARGET_SPEED and len(v) >= 3:
            s = Ftms.u16(v, 1) / 100.0
            if self.cfg.mphMode:
                s = s * Ftms.MPH_TO_KMH
            self.st.machineTargetSpeed = s
            if self.follower is not None:
                self.follower.onMachineTarget(True, s)
        elif op == Ftms.ST_TARGET_INCLINE and len(v) >= 3:
            p = Ftms.s16(v, 1) / 10.0
            self.st.machineTargetIncline = p
            if self.follower is not None:
                self.follower.onMachineTarget(False, p)
        elif op == Ftms.ST_CONTROL_LOST:
            self.hasControl = False
            self.controlRequested = False
            self.problem = "Control lost"

    # BleDelegate
    def onProfileRegister(self, uuid, status):
        if self.link != LINK_REGISTERING:
            return
        if status == STATUS_SUCCESS:
            self.startScan()
        else:
            self.profileFailed()

    def onScanStateChange(self, scanState, status):
        self.scanning = scanState == SCAN_STATE_SCANNING

    def onScanResults(self, results):
        if self.link != LINK_SCANNING:
            return
        flt = self.cfg.nameFilter.lower()
        for r in results:
            name = r.name or ""
            self.noteSeen(r, name)
            if self.isExcluded(r):
                continue
            lname = name.lower()
            proven = False
            if len(flt) > 0:
                if lname.find(flt) < 0:
                    continue
                proven = True
            elif self.advertisesFtms(r):
                proven = True
            elif not self.looksLikeTreadmill(lname):
                continue
            known = False
            for i, c in enumerate(self.candidates):
                if r.isSameDevice(c[0]):
                    self.candidates[i] = [r, r.rssi, name, proven]
                    known = True
                    break
            if not known and len(self.candidates) < 8:
                self.candidates.append([r, r.rssi, name, proven])

    def onConnectedStateChanged(self, dev, state):
        if state == CONNECTION_STATE_CONNECTED:
            if self.link in (LINK_CONNECTING, LINK_RECONNECTING):
                self.device = dev
                self.onLinkUp()
        elif self.link in (LINK_DISCOVERING, LINK_SETUP, LINK_READY):
            self.onLinkDown()

    def onCharacteristicRead(self, ch, status, value):
        self.busy = False
        self.busyOp = None
        if status == STATUS_SUCCESS and value is not None:
            u = ch.uuid
            if u == self.featUuid:
                f = Ftms.parseFeature(value)
                if f is not None:
                    self.machineFeatures, self.targetFeatures = f
                    if self.targetFeatures != 0:
                        self.speedSupported = (self.targetFeatures & 0x01) != 0
                        self.inclineSupported = (self.targetFeatures & 0x02) != 0
            elif u == self.speedRangeUuid:
                Ftms.parseSpeedRange(value, self.st, self.cfg.mphMode)
            elif u == self.inclRangeUuid:
                Ftms.parseInclineRange(value, self.st)
        self.pump()

    def onCharacteristicWrite(self, ch, status):
        wasCp = self.busyOp is not None and self.busyOp[0] == OP_CP
        op = self.busyOp[2][0] if wasCp else -1
        self.busy = False
        self.busyOp = None
        if wasCp and status != STATUS_SUCCESS:
            self.onCpResult(op, -2)
        elif wasCp and self.noIndications:
            self.onCpResult(op, Ftms.RES_SUCCESS)
        self.pump()

    def onDescriptorWrite(self, desc, status):
        self.busy = False
        self.busyOp = None
        self.pump()

    def onCharacteristicChanged(self, ch, value):
        if value is None:
            return
        u = ch.uuid
        if u == self.dataUuid:
            Ftms.parseTreadmillData(value, self.st, self.cfg.mphMode)
        elif u == self.cpUuid:
            if len(value) >= 3 and value[0] == Ftms.CP_RESPONSE:
                self.onCpResult(value[1], value[2])
                self.pump()
        elif u == self.statusUuid:
            self.onStatus(value)


# ---------------------------------------------------------------- WorkoutFollower.mc
class WorkoutFollower:
    def __init__(self, mgr, st, cfg, activity):
        self.activity = activity          # object with getCurrentWorkoutStep()
        self.inWorkout = False
        self.stepName = ""
        self.stepSource = ""
        self.targetSpeed = None
        self.targetIncline = None
        self.overrideSpeed = False
        self.overrideIncline = False
        self.speedProblem = ""
        self.inclineProblem = ""
        self.mgr = mgr
        self.st = st
        self.cfg = cfg
        self.sig = None
        self.dirty = False
        self.pendingApply = False
        self.manualSticky = False
        self.timerOn = False
        self.wasRunning = False
        self.remoteStartPending = False
        self.remoteStartSince = 0
        self.cmdSpeed = None
        self.cmdSpeedMs = 0
        self.cmdIncline = None
        self.cmdInclineMs = 0
        self.speedDev = 0
        self.inclineDev = 0
        self.lastSpeedSample = -1.0
        self.lastInclineSample = -100.0

    def tick(self, isTimerOn):
        now = get_timer()
        self.timerOn = isTimerOn
        if self.remoteStartPending and now - self.remoteStartSince > 10000:
            self.remoteStartPending = False
        self.readStep()
        running = self.st.beltRunning()
        if running and not self.wasRunning:
            self.pendingApply = True
        self.wasRunning = running
        if not self.cfg.controlEnabled or not self.inWorkout or not self.timerOn \
                or self.st.safetyKey or not self.mgr.canControl():
            return
        if not running:
            if self.remoteStartPending and self.cfg.remoteStart and self.targetSpeed is not None:
                self.remoteStartPending = False
                self.mgr.requestStartResume()
                self.pendingApply = True
            return
        self.remoteStartPending = False
        if self.pendingApply:
            self.apply(now)
        self.watchForOverride(now)

    def modeText(self):
        if not self.cfg.controlEnabled: return "READ ONLY"
        if not self.inWorkout: return ""
        if self.mgr.isReady() and not self.mgr.controllable(): return "READ ONLY"
        if self.mgr.controlDenied: return "NO CONTROL"
        if self.overrideSpeed or self.overrideIncline: return "MANUAL"
        if self.refusedNow(): return "REFUSED"
        if self.targetSpeed is None and self.targetIncline is None: return "OPEN"
        return "AUTO"

    def refusedNow(self):
        return (self.targetSpeed is not None and len(self.speedProblem) > 0) \
            or (self.targetIncline is not None and len(self.inclineProblem) > 0)

    def refusedText(self):
        if self.targetSpeed is not None and len(self.speedProblem) > 0:
            return self.speedProblem
        return self.inclineProblem

    def onTimerStart(self):
        self.pendingApply = True
        self.armRemoteStart()

    def onTimerResume(self):
        self.overrideSpeed = self.manualSticky
        self.overrideIncline = self.manualSticky
        self.pendingApply = True
        self.armRemoteStart()

    def onTimerPause(self):
        self.remoteStartPending = False
        if self.cfg.pauseWithWatch and self.cfg.controlEnabled and self.st.beltRunning() and self.mgr.canControl():
            self.mgr.requestPause()

    def onStepComplete(self):
        self.dirty = True

    def onLinkReady(self):
        self.pendingApply = True

    def onCommandResult(self, isSpeed, result):
        text = ""
        if result != Ftms.RES_SUCCESS and result != -1:
            text = ("Speed " if isSpeed else "Incline ") + Ftms.resultText(result)
        if isSpeed:
            self.speedProblem = text
        else:
            self.inclineProblem = text
        if result == Ftms.RES_SUCCESS or result == -1:
            if isSpeed:
                self.cmdSpeedMs = get_timer()
            else:
                self.cmdInclineMs = get_timer()
        else:
            if isSpeed:
                self.cmdSpeed = None
            else:
                self.cmdIncline = None

    def onMachineTarget(self, isSpeed, value):
        if not self.timerOn:
            return
        now = get_timer()
        if isSpeed:
            if self.cmdSpeed is not None and not self.overrideSpeed and now - self.cmdSpeedMs > 2500 \
                    and abs(value - self.cmdSpeed) > 0.15:
                self.setOverride(True)
        else:
            if self.cmdIncline is not None and not self.overrideIncline and now - self.cmdInclineMs > 2500 \
                    and abs(value - self.cmdIncline) > 0.3:
                self.setOverride(False)

    def armRemoteStart(self):
        self.remoteStartPending = self.cfg.remoteStart
        self.remoteStartSince = get_timer()

    def readStep(self):
        info = self.activity.getCurrentWorkoutStep()
        if info is None:
            if self.inWorkout:
                self.inWorkout = False
                self.sig = None
                self.stepName = ""
                self.stepSource = ""
                self.targetSpeed = None
                self.targetIncline = None
                self.cmdSpeed = None
                self.cmdIncline = None
            return
        self.inWorkout = True
        s = self.signature(info)
        if self.dirty or self.sig is None or s != self.sig:
            self.dirty = False
            self.sig = s
            self.onNewStep(info)

    def signature(self, info):
        s = mc_str(info.intensity) + "|" + mc_str(info.name) + "|" + mc_str(info.notes)
        step = info.step
        if isinstance(step, WorkoutStep):
            s += "|%s|%s|%s|%s|%s" % (step.targetType, step.targetValueLow, step.targetValueHigh,
                                      step.durationType, step.durationValue)
        return s

    def onNewStep(self, info):
        self.stepName = info.name if info.name is not None else ""
        self.stepSource = ""
        speed = None
        incline = None
        if self.cfg.useNotes:
            text = info.notes if info.notes is not None else ""
            text = text + " " + self.stepName
            speed = Targets.speedFromText(text)
            incline = Targets.inclineFromText(text)
            if speed is not None or incline is not None:
                self.stepSource = "notes"
        if speed is None and self.cfg.usePaceTarget:
            speed = Targets.speedFromStep(info)
            if speed is not None and len(self.stepSource) == 0:
                self.stepSource = "pace"
        if incline is None and speed is not None and self.cfg.defaultIncline >= 0.0:
            incline = self.cfg.defaultIncline
        self.targetSpeed = Targets.clampSpeed(speed, self.st, self.cfg.maxSpeed) if speed is not None else None
        self.targetIncline = Targets.clampIncline(incline, self.st, self.cfg.maxIncline) if incline is not None else None
        self.overrideSpeed = self.manualSticky
        self.overrideIncline = self.manualSticky
        self.cmdSpeed = None
        self.cmdIncline = None
        self.speedDev = 0
        self.inclineDev = 0
        self.pendingApply = True
        self.mgr.clearControlDenied()

    def apply(self, now):
        self.pendingApply = False
        if self.targetSpeed is not None and not self.overrideSpeed and self.mgr.speedSupported:
            if self.mgr.requestSpeed(self.targetSpeed):
                self.cmdSpeed = self.targetSpeed
                self.cmdSpeedMs = now
                self.speedDev = 0
        if self.targetIncline is not None and not self.overrideIncline and self.mgr.inclineSupported:
            if self.mgr.requestIncline(self.targetIncline):
                self.cmdIncline = self.targetIncline
                self.cmdInclineMs = now
                self.inclineDev = 0

    def watchForOverride(self, now):
        if self.cmdSpeed is not None and not self.overrideSpeed and self.st.hasSpeed and now - self.cmdSpeedMs > 5000:
            steady = abs(self.st.speedKmh - self.lastSpeedSample) < 0.05
            if steady and abs(self.st.speedKmh - self.cmdSpeed) > 0.35:
                self.speedDev += 1
                if self.speedDev >= 5:
                    self.setOverride(True)
            else:
                self.speedDev = 0
        self.lastSpeedSample = self.st.speedKmh
        if self.cmdIncline is not None and not self.overrideIncline and self.st.hasIncline and now - self.cmdInclineMs > 10000:
            steady = abs(self.st.incline - self.lastInclineSample) < 0.05
            if steady and abs(self.st.incline - self.cmdIncline) > 0.6:
                self.inclineDev += 1
                if self.inclineDev >= 8:
                    self.setOverride(False)
            else:
                self.inclineDev = 0
        self.lastInclineSample = self.st.incline

    def setOverride(self, isSpeed):
        if isSpeed:
            self.overrideSpeed = True
        else:
            self.overrideIncline = True
        if not self.cfg.autoResume:
            self.manualSticky = True
