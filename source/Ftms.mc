// Decoding and encoding of Bluetooth Fitness Machine Service (FTMS, 0x1826) packets.
// Pure functions only: no BLE calls, so the logic can be mirrored and unit tested
// (see tests/ftms_logic.py, which is a line-by-line port of this file and Targets.mc).

using Toybox.Math;

module Ftms {

    // Treadmill Data (0x2ACD) flag bits
    const F_MORE_DATA = 0x0001;    // 0 = Instantaneous Speed field IS present
    const F_AVG_SPEED = 0x0002;
    const F_TOTAL_DISTANCE = 0x0004;
    const F_INCLINATION = 0x0008;  // inclination + ramp angle
    const F_ELEVATION = 0x0010;    // positive + negative elevation gain
    const F_INST_PACE = 0x0020;
    const F_AVG_PACE = 0x0040;
    const F_ENERGY = 0x0080;       // total, per hour, per minute
    const F_HEART_RATE = 0x0100;
    const F_MET = 0x0200;
    const F_ELAPSED = 0x0400;
    const F_REMAINING = 0x0800;
    const F_FORCE_POWER = 0x1000;

    // Bits returned by parseTreadmillData() telling which values the packet carried
    const HAS_SPEED = 1;
    const HAS_DISTANCE = 2;
    const HAS_INCLINE = 4;
    const HAS_ELEVATION = 8;

    // Control Point (0x2AD9) op codes
    const CP_REQUEST_CONTROL = 0x00;
    const CP_RESET = 0x01;
    const CP_SET_SPEED = 0x02;
    const CP_SET_INCLINE = 0x03;
    const CP_START_RESUME = 0x07;
    const CP_STOP_PAUSE = 0x08;
    const CP_RESPONSE = 0x80;

    // Control Point result codes
    const RES_SUCCESS = 0x01;
    const RES_NOT_SUPPORTED = 0x02;
    const RES_INVALID_PARAM = 0x03;
    const RES_FAILED = 0x04;
    const RES_NOT_PERMITTED = 0x05;

    // Fitness Machine Status (0x2ADA) op codes
    const ST_RESET = 0x01;
    const ST_STOPPED_BY_USER = 0x02;
    const ST_SAFETY_KEY = 0x03;
    const ST_STARTED_BY_USER = 0x04;
    const ST_TARGET_SPEED = 0x05;
    const ST_TARGET_INCLINE = 0x06;
    const ST_CONTROL_LOST = 0xFF;

    const MPH_TO_KMH = 1.609344;

    function u16(b, i) {
        return b[i] | (b[i + 1] << 8);
    }

    function s16(b, i) {
        var v = b[i] | (b[i + 1] << 8);
        if (v >= 0x8000) {
            v = v - 0x10000;
        }
        return v;
    }

    function u24(b, i) {
        return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16);
    }

    function u32(b, i) {
        return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24);
    }

    // Decodes one Treadmill Data notification into st (a TreadmillState).
    // Returns a HAS_* bit mask, or -1 for a malformed packet.
    // A machine may split its data over several packets (More Data bit); every
    // packet is self-describing, so each one is decoded on its own.
    function parseTreadmillData(b, st, mph) {
        var n = b.size();
        if (n < 2) {
            return -1;
        }
        var flags = u16(b, 0);
        var i = 2;
        var got = 0;
        var scale = mph ? MPH_TO_KMH : 1.0;

        if ((flags & F_MORE_DATA) == 0) {
            if (i + 2 > n) { return got; }
            st.setSpeed(u16(b, i) / 100.0 * scale);
            got |= HAS_SPEED;
            i += 2;
        }
        if ((flags & F_AVG_SPEED) != 0) {
            if (i + 2 > n) { return got; }
            i += 2;
        }
        if ((flags & F_TOTAL_DISTANCE) != 0) {
            if (i + 3 > n) { return got; }
            st.onMachineDistance(u24(b, i));
            got |= HAS_DISTANCE;
            i += 3;
        }
        if ((flags & F_INCLINATION) != 0) {
            if (i + 4 > n) { return got; }
            var raw = s16(b, i);
            if (raw != 0x7FFF) {           // 0x7FFF = "data not available"
                st.setIncline(raw / 10.0);
                got |= HAS_INCLINE;
            }
            i += 4;                        // ramp angle (sint16) is skipped
        }
        if ((flags & F_ELEVATION) != 0) {
            if (i + 4 > n) { return got; }
            st.onMachineElevation(u16(b, i) / 10.0);
            got |= HAS_ELEVATION;
            i += 4;                        // negative elevation gain is skipped
        }
        if ((flags & F_INST_PACE) != 0) { i += 1; }
        if ((flags & F_AVG_PACE) != 0) { i += 1; }
        if ((flags & F_ENERGY) != 0) {
            if (i + 5 > n) { return got; }
            var kcal = u16(b, i);
            if (kcal != 0xFFFF) {
                st.machineEnergy = kcal;
            }
            i += 5;
        }
        if ((flags & F_HEART_RATE) != 0) {
            if (i + 1 > n) { return got; }
            st.machineHr = b[i];
            i += 1;
        }
        if ((flags & F_MET) != 0) { i += 1; }
        if ((flags & F_ELAPSED) != 0) {
            if (i + 2 > n) { return got; }
            st.machineElapsed = u16(b, i);
            i += 2;
        }
        return got;
    }

    // Fitness Machine Feature (0x2ACC): [machine features uint32, target setting features uint32]
    // Returns [machineFeatures, targetFeatures] or null.
    function parseFeature(b) {
        if (b.size() < 8) {
            return null;
        }
        return [u32(b, 0), u32(b, 4)];
    }

    // Supported Speed Range (0x2AD4): min, max, increment, all uint16 in 0.01 km/h
    function parseSpeedRange(b, st, mph) {
        if (b.size() < 6) {
            return false;
        }
        var scale = mph ? MPH_TO_KMH : 1.0;
        var mn = u16(b, 0) / 100.0 * scale;
        var mx = u16(b, 2) / 100.0 * scale;
        var inc = u16(b, 4) / 100.0 * scale;
        if (mx <= mn || mx <= 0.0) {
            return false;                  // nonsense, keep the defaults
        }
        st.minSpeed = mn;
        st.maxSpeed = mx;
        if (inc > 0.0 && inc <= 1.0) {
            st.speedInc = inc;
        }
        return true;
    }

    // Supported Inclination Range (0x2AD5): min sint16, max sint16 (0.1 %), increment uint16 (0.1 %)
    function parseInclineRange(b, st) {
        if (b.size() < 6) {
            return false;
        }
        var mn = s16(b, 0) / 10.0;
        var mx = s16(b, 2) / 10.0;
        var inc = u16(b, 4) / 10.0;
        if (mx <= mn) {
            return false;
        }
        st.minIncline = mn;
        st.maxIncline = mx;
        if (inc > 0.0 && inc <= 5.0) {
            st.inclineInc = inc;
        }
        return true;
    }

    function encodeSpeed(kmh, mph) {
        var v = mph ? kmh / MPH_TO_KMH : kmh;
        var raw = Math.round(v * 100.0).toNumber();
        if (raw < 0) {
            raw = 0;
        }
        var b = new [3]b;
        b[0] = CP_SET_SPEED;
        b[1] = raw & 0xFF;
        b[2] = (raw >> 8) & 0xFF;
        return b;
    }

    function encodeIncline(pct) {
        var raw = Math.round(pct * 10.0).toNumber();
        if (raw < 0) {
            raw = raw + 0x10000;           // two's complement sint16
        }
        var b = new [3]b;
        b[0] = CP_SET_INCLINE;
        b[1] = raw & 0xFF;
        b[2] = (raw >> 8) & 0xFF;
        return b;
    }

    // Does raw advertising data (AD structures: length, type, data) mention service 0x1826?
    // Checks 16-bit UUID lists (types 0x02/0x03), 16-bit service data (0x16) and
    // 128-bit UUID lists (0x06/0x07, base UUID 0000xxxx-0000-1000-8000-00805f9b34fb).
    function rawHasFtms(raw) {
        if (raw == null) {
            return false;
        }
        var n = raw.size();
        var i = 0;
        while (i < n) {
            var len = raw[i];
            if (len == 0 || i + len >= n) {
                break;                     // end padding or a truncated structure
            }
            var type = raw[i + 1];
            if (type == 0x02 || type == 0x03) {
                for (var j = i + 2; j + 1 <= i + len; j += 2) {
                    if (raw[j] == 0x26 && raw[j + 1] == 0x18) {
                        return true;
                    }
                }
            } else if (type == 0x16) {
                if (len >= 3 && raw[i + 2] == 0x26 && raw[i + 3] == 0x18) {
                    return true;
                }
            } else if (type == 0x06 || type == 0x07) {
                for (var k = i + 2; k + 15 <= i + len; k += 16) {
                    if (raw[k] == 0xFB && raw[k + 12] == 0x26 && raw[k + 13] == 0x18
                            && raw[k + 14] == 0x00 && raw[k + 15] == 0x00) {
                        return true;
                    }
                }
            }
            i += len + 1;
        }
        return false;
    }

    function resultText(code) {
        if (code == RES_SUCCESS) { return "OK"; }
        if (code == RES_NOT_SUPPORTED) { return "Not supported"; }
        if (code == RES_INVALID_PARAM) { return "Bad value"; }
        if (code == RES_FAILED) { return "Refused"; }
        if (code == RES_NOT_PERMITTED) { return "No control"; }
        if (code == -2) { return "Write failed"; }
        return "No reply";
    }
}
