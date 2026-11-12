using Toybox.WatchUi as Ui;
using Toybox.Graphics as Gfx;
using Toybox.Activity;
using Toybox.System as Sys;

// The data field itself. Works in any size slot; a full-screen or half-screen slot
// shows everything:
//
//      AUTO MATRIX-12         <- status (green AUTO / orange MANUAL / red SAFETY KEY)
//          13.3               <- belt speed (or pace, see settings)
//      km/h  4:31 /km
//      1.0%      2.35 km      <- incline, treadmill distance this activity
//      Tgt 13.3 1.0% notes    <- what the current step asks for, and where it came from
class TreadmillView extends Ui.DataField {

    hidden var mgr;
    hidden var st;
    hidden var cfg;
    hidden var follower;
    hidden var rec = null;
    hidden var lastMs = 0;

    function initialize(manager, state, settings) {
        DataField.initialize();
        mgr = manager;
        st = state;
        cfg = settings;
        follower = new WorkoutFollower(mgr, st, cfg);
        mgr.follower = follower;
        if (cfg.recordFit) {
            try {
                rec = new FitRecorder(self);
            } catch (e) {
                rec = null;                // recording is optional, never fatal
            }
        }
    }

    function compute(info) {
        var now = Sys.getTimer();
        var dt = 0.0;
        if (lastMs != 0) {
            dt = (now - lastMs) / 1000.0;
            if (dt > 5.0 || dt < 0.0) {
                dt = 0.0;
            }
        }
        lastMs = now;
        var timerOn = false;
        if (info != null && (info has :timerState) && info.timerState != null) {
            timerOn = (info.timerState == Activity.TIMER_STATE_ON);
        }
        mgr.tick();
        st.tick(timerOn, dt);
        follower.tick(timerOn);
        if (rec != null) {
            rec.update(st, follower);
        }
    }

    // ---------------------------------------------------------------- timer events

    function onTimerStart() {
        follower.onTimerStart();
    }

    function onTimerResume() {
        follower.onTimerResume();
    }

    function onTimerPause() {
        follower.onTimerPause();
    }

    function onTimerStop() {
        follower.onTimerPause();
    }

    function onTimerLap() {
        st.lapReset();                     // new lap for the lap fields; not a workout step change
    }

    function onTimerReset() {
        st.sessionReset();
    }

    function onWorkoutStepComplete() {
        follower.onStepComplete();
    }

    // ---------------------------------------------------------------- drawing

    function onUpdate(dc) {
        var bg = getBackgroundColor();
        var fg = (bg == Gfx.COLOR_BLACK) ? Gfx.COLOR_WHITE : Gfx.COLOR_BLACK;
        dc.setColor(fg, bg);
        dc.clear();

        var w = dc.getWidth();
        var h = dc.getHeight();
        var cx = w / 2;
        var fresh = st.isFresh() && st.hasSpeed;

        var main;
        var sub;
        if (cfg.mainValue == 1) {
            main = fresh ? Fmt.pace(st.speedKmh) : "--:--";
            sub = fresh ? "/km  " + Fmt.one(st.speedKmh) + " km/h" : "/km";
        } else {
            main = fresh ? Fmt.one(st.speedKmh) : "--.-";
            sub = fresh ? "km/h  " + Fmt.pace(st.speedKmh) + " /km" : "km/h";
        }
        var incl = (st.isFresh() && st.hasIncline) ? Fmt.one(st.incline) + "%" : "--%";
        var dist = (st.sessDist / 1000.0).format("%.2f") + " km";
        var status = statusLine();
        var statusColor = statusColorFor(bg, fg);

        var fx = Gfx.FONT_XTINY;
        var hx = dc.getFontHeight(fx);
        var fs = Gfx.FONT_SMALL;
        var hs = dc.getFontHeight(fs);

        var fm;
        var hm;
        var total;
        var y;
        if (h >= 4 * hx + hs + dc.getFontHeight(Gfx.FONT_NUMBER_MILD) && w >= 140) {
            // large slot: five lines
            fm = pickNumberFont(dc, main, w, h - 3 * hx - hs - 4);
            hm = dc.getFontHeight(fm);
            total = hx + hm + hx + hs + hx;
            y = (h - total) / 2;
            drawLine(dc, cx, y, hx, fx, status, statusColor, fg);
            y += hx;
            drawLine(dc, cx, y, hm, fm, main, fg, fg);
            y += hm;
            drawLine(dc, cx, y, hx, fx, sub, fg, fg);
            y += hx;
            drawLine(dc, cx, y, hs, fs, incl + "   " + dist, fg, fg);
            y += hs;
            drawLine(dc, cx, y, hx, fx, targetLine(), fg, fg);
        } else if (h >= 2 * hx + dc.getFontHeight(Gfx.FONT_NUMBER_MILD) * 0.8) {
            // medium slot: status / value / incline + distance
            fm = pickNumberFont(dc, main, w, h - 2 * hx);
            hm = dc.getFontHeight(fm);
            total = hx + hm + hx;
            y = (h - total) / 2;
            drawLine(dc, cx, y, hx, fx, status, statusColor, fg);
            y += hx;
            drawLine(dc, cx, y, hm, fm, main, fg, fg);
            y += hm;
            drawLine(dc, cx, y, hx, fx, mgr.isReady() ? incl + "  " + dist : mgr.diagText(), fg, fg);
        } else {
            // tiny slot: value and incline on one line
            var f = Gfx.FONT_SMALL;
            if (dc.getFontHeight(Gfx.FONT_MEDIUM) <= h) {
                f = Gfx.FONT_MEDIUM;
            }
            drawLine(dc, cx, (h - dc.getFontHeight(f)) / 2, dc.getFontHeight(f), f,
                mgr.isReady() ? main + " " + incl : status, mgr.isReady() ? fg : statusColor, fg);
        }
    }

    hidden function drawLine(dc, x, top, lineH, font, text, color, fg) {
        dc.setColor(color, Gfx.COLOR_TRANSPARENT);
        dc.drawText(x, top + lineH / 2, font, text, Gfx.TEXT_JUSTIFY_CENTER | Gfx.TEXT_JUSTIFY_VCENTER);
        dc.setColor(fg, Gfx.COLOR_TRANSPARENT);
    }

    // largest number font that fits both the height and the width
    hidden function pickNumberFont(dc, text, w, maxH) {
        var fonts = [Gfx.FONT_NUMBER_THAI_HOT, Gfx.FONT_NUMBER_HOT, Gfx.FONT_NUMBER_MEDIUM, Gfx.FONT_NUMBER_MILD];
        for (var i = 0; i < fonts.size(); i++) {
            if (dc.getFontHeight(fonts[i]) <= maxH && dc.getTextWidthInPixels(text, fonts[i]) <= w * 0.85) {
                return fonts[i];
            }
        }
        return Gfx.FONT_MEDIUM;
    }

    hidden function statusLine() {
        if (!mgr.isReady()) {
            return mgr.statusText();
        }
        if (st.safetyKey) {
            return "SAFETY KEY";
        }
        if (!st.isFresh()) {
            return "No data";
        }
        var name = mgr.statusText();
        if (name.length() > 12) {
            name = name.substring(0, 12);
        }
        var mode = follower.modeText();
        return mode.length() > 0 ? mode + " " + name : name;
    }

    hidden function statusColorFor(bg, fg) {
        var dark = (bg == Gfx.COLOR_BLACK);
        if (!mgr.isReady()) {
            return mgr.isError() ? Gfx.COLOR_RED : fg;
        }
        if (st.safetyKey) {
            return Gfx.COLOR_RED;
        }
        var mode = follower.modeText();
        if (mode.equals("MANUAL") || mode.equals("NO CONTROL") || mode.equals("REFUSED")) {
            return Gfx.COLOR_ORANGE;
        }
        if (mode.equals("AUTO")) {
            return dark ? Gfx.COLOR_GREEN : Gfx.COLOR_DK_GREEN;
        }
        return fg;
    }

    hidden function targetLine() {
        if (!mgr.isReady()) {
            return mgr.diagText();               // while searching: what the watch can hear
        }
        if (follower.refusedNow()) {
            return follower.refusedText();       // e.g. "Speed Refused"
        }
        if (follower.targetSpeed != null || follower.targetIncline != null) {
            var s = "Tgt";
            if (follower.targetSpeed != null) {
                s += " " + Fmt.one(follower.targetSpeed);
            }
            if (follower.targetIncline != null) {
                s += " " + Fmt.one(follower.targetIncline) + "%";
            }
            if (follower.stepSource.length() > 0) {
                s += " " + follower.stepSource;    // "notes" or "pace": where the target came from
            }
            return s;
        }
        if (mgr.isReady() && mgr.problem.length() > 0) {
            return mgr.problem;
        }
        return follower.stepName;
    }
}
