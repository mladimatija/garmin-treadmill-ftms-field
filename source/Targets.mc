// Works out what the treadmill should be set to for a Garmin workout step.
//   1. Step notes (and name): "12.0 km/h", "12 kph", "7.5 mph", "1% incline", "-1 %" ...
//   2. Otherwise the step's pace/speed target: the middle of the range.
// Pure functions; mirrored in tests/ftms_logic.py.

using Toybox.Activity;
using Toybox.Math;

module Targets {

    const C_SPACE = 32;
    const C_MINUS = 45;
    const C_DOT = 46;
    const C_COMMA = 44;
    const C_0 = 48;
    const C_9 = 57;

    // km/h from free text, or null
    function speedFromText(text) {
        if (text == null) {
            return null;
        }
        var t = text.toLower();
        var v = numberBefore(t, "km/h");
        if (v == null) { v = numberBefore(t, "kph"); }
        if (v == null) { v = numberBefore(t, "kmh"); }
        if (v == null) {
            var m = numberBefore(t, "mph");
            if (m != null) {
                v = m * 1.609344;
            }
        }
        if (v != null && (v < 0.5 || v > 30.0)) {
            return null;                   // not a treadmill speed
        }
        return v;
    }

    // incline % from free text, or null
    function inclineFromText(text) {
        if (text == null) {
            return null;
        }
        var v = numberBefore(text.toLower(), "%");
        if (v != null && (v < -10.0 || v > 40.0)) {
            return null;
        }
        return v;
    }

    // The number written just before the first occurrence of unit ("13.3 km/h" -> 13.3).
    // Accepts a decimal comma and a leading minus. Spaces between number and unit are allowed.
    function numberBefore(t, unit) {
        var idx = t.find(unit);
        if (idx == null) {
            return null;
        }
        var chars = t.toCharArray();
        var end = idx;
        while (end > 0 && chars[end - 1].toNumber() == C_SPACE) {
            end -= 1;
        }
        var start = end;
        while (start > 0) {
            var ch = chars[start - 1].toNumber();
            if ((ch >= C_0 && ch <= C_9) || ch == C_DOT || ch == C_COMMA) {
                start -= 1;
            } else {
                break;
            }
        }
        // drop separators that are not decimal points, e.g. "(2 km, 13.3" or "1." at a sentence end
        while (start < end && !isDigit(chars[start].toNumber())) {
            start += 1;
        }
        while (end > start && !isDigit(chars[end - 1].toNumber())) {
            end -= 1;
        }
        if (start >= end) {
            return null;
        }
        var s = "";
        for (var i = start; i < end; i++) {
            s += (chars[i].toNumber() == C_COMMA) ? "." : chars[i].toString();
        }
        var v = s.toFloat();
        if (v == null) {
            return null;
        }
        if (start > 0 && chars[start - 1].toNumber() == C_MINUS) {
            v = -v;
        }
        return v;
    }

    function isDigit(c) {
        return c >= C_0 && c <= C_9;
    }

    // km/h from the step's speed/pace target, or null when the step has none.
    // Pace targets are stored by Garmin as a speed range in m/s.
    function speedFromStep(info) {
        var s = info.step;
        if (s == null) {
            return null;
        }
        if (s instanceof Activity.WorkoutIntervalStep) {
            var it = info.intensity;
            // 1 = rest, 4 = recovery (numbers on purpose: the enum names have crashed on some firmware)
            s = (it == 1 || it == 4) ? s.restStep : s.activeStep;
            if (s == null) {
                return null;
            }
        }
        if (!(s instanceof Activity.WorkoutStep)) {
            return null;
        }
        return speedFromTarget(s.targetType, s.targetValueLow, s.targetValueHigh);
    }

    // targetType 0 = speed. lo/hi in m/s (mm/s also accepted, defensively).
    function speedFromTarget(targetType, lo, hi) {
        if (targetType != 0) {
            return null;
        }
        var a = toMps(lo);
        var b = toMps(hi);
        if (a <= 0.0 || b <= 0.0) {
            return null;                   // open-ended or missing: don't guess a speed
        }
        if (a == b && a <= 10.0 && a == a.toNumber().toFloat()) {
            return null;                   // looks like a pace zone number, not a speed
        }
        var mps = (a + b) / 2.0;
        if (mps < 0.3 || mps > 8.5) {
            return null;
        }
        return mps * 3.6;
    }

    function toMps(v) {
        if (v == null) {
            return 0.0;
        }
        var f = v.toFloat();
        if (f > 50.0) {
            f = f / 1000.0;
        }
        return f;
    }

    function roundTo(v, inc) {
        if (inc <= 0.0) {
            return v;
        }
        return Math.round(v / inc) * inc;
    }

    // Clamp to the machine range and the user's own limit, then snap to the machine's step size.
    function clampSpeed(v, st, userMax) {
        var mx = st.maxSpeed < userMax ? st.maxSpeed : userMax;
        var r = roundTo(clamp(v, st.minSpeed, mx), st.speedInc);
        if (r > mx + 0.001) {
            r -= st.speedInc;
        }
        return r;
    }

    function clampIncline(v, st, userMax) {
        var mx = st.maxIncline < userMax ? st.maxIncline : userMax;
        var r = roundTo(clamp(v, st.minIncline, mx), st.inclineInc);
        if (r > mx + 0.001) {
            r -= st.inclineInc;
        }
        if (r < st.minIncline - 0.001) {
            r += st.inclineInc;
        }
        return r;
    }

    function clamp(v, lo, hi) {
        if (v < lo) {
            return lo;
        }
        if (v > hi) {
            return hi;
        }
        return v;
    }
}

module Fmt {

    function pace(kmh) {
        if (kmh == null || kmh < 0.5) {
            return "--:--";
        }
        var secs = Math.round(3600.0 / kmh).toNumber();
        return (secs / 60).format("%d") + ":" + (secs % 60).format("%02d");
    }

    function one(v) {
        return v.format("%.1f");
    }
}
