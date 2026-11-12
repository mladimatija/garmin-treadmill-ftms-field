using Toybox.Activity;
using Toybox.System as Sys;

// Follows the Garmin workout that is running on the watch and sends each step's
// speed and incline to the treadmill.
//
// Safety rules (see README):
//   * commands are only sent while the watch timer runs AND the belt is already moving;
//   * the belt is never started from the watch unless "Start belt from watch" is on;
//   * touching speed or incline on the console = manual override: the field stops sending
//     for the rest of that step (or the rest of the workout if "Auto resume" is off);
//   * the console Stop button and the safety key always win, the field never undoes them.
class WorkoutFollower {

    var inWorkout = false;
    var stepName = "";
    var stepSource = "";          // "notes", "pace" or ""
    var targetSpeed = null;       // km/h, clamped, for the current step
    var targetIncline = null;     // %
    var overrideSpeed = false;
    var overrideIncline = false;
    var speedProblem = "";        // last speed command failed for good, e.g. "Speed Refused"
    var inclineProblem = "";

    hidden var mgr;
    hidden var st;
    hidden var cfg;
    hidden var sig = null;
    hidden var dirty = false;
    hidden var pendingApply = false;
    hidden var manualSticky = false;
    hidden var timerOn = false;
    hidden var wasRunning = false;
    hidden var remoteStartPending = false;
    hidden var remoteStartSince = 0;
    hidden var cmdSpeed = null;
    hidden var cmdSpeedMs = 0;
    hidden var cmdIncline = null;
    hidden var cmdInclineMs = 0;
    hidden var speedDev = 0;
    hidden var inclineDev = 0;
    hidden var lastSpeedSample = -1.0;
    hidden var lastInclineSample = -100.0;

    function initialize(manager, state, settings) {
        mgr = manager;
        st = state;
        cfg = settings;
    }

    // Once per second from compute().
    function tick(isTimerOn) {
        var now = Sys.getTimer();
        timerOn = isTimerOn;
        if (remoteStartPending && now - remoteStartSince > 10000) {
            remoteStartPending = false;    // only right after Start/Resume, never minutes later
        }
        readStep();

        var running = st.beltRunning();
        if (running && !wasRunning) {
            pendingApply = true;           // belt (re)started: bring it to the step's targets
        }
        wasRunning = running;

        if (!cfg.controlEnabled || !inWorkout || !timerOn || st.safetyKey || !mgr.canControl()) {
            return;
        }
        if (!running) {
            if (remoteStartPending && cfg.remoteStart && targetSpeed != null) {
                remoteStartPending = false;
                mgr.requestStartResume();
                pendingApply = true;
            }
            return;
        }
        remoteStartPending = false;
        if (pendingApply) {
            apply(now);
        }
        watchForOverride(now);
    }

    function modeText() {
        if (!cfg.controlEnabled) { return "READ ONLY"; }
        if (!inWorkout) { return ""; }
        if (mgr.isReady() && !mgr.controllable()) { return "READ ONLY"; }
        if (mgr.controlDenied) { return "NO CONTROL"; }
        if (overrideSpeed || overrideIncline) { return "MANUAL"; }
        if (refusedNow()) { return "REFUSED"; }
        if (targetSpeed == null && targetIncline == null) { return "OPEN"; }
        return "AUTO";
    }

    // The current step asks for something the treadmill turned down.
    function refusedNow() {
        return (targetSpeed != null && speedProblem.length() > 0)
            || (targetIncline != null && inclineProblem.length() > 0);
    }

    function refusedText() {
        if (targetSpeed != null && speedProblem.length() > 0) {
            return speedProblem;
        }
        return inclineProblem;
    }

    // ---------------------------------------------------------------- events

    function onTimerStart() {
        pendingApply = true;
        armRemoteStart();
    }

    // Resuming the watch = carry on with the workout: changes made on the console while
    // paused (slowing to a walk, say) are not treated as an override.
    function onTimerResume() {
        overrideSpeed = manualSticky;
        overrideIncline = manualSticky;
        pendingApply = true;
        armRemoteStart();
    }

    function onTimerPause() {
        remoteStartPending = false;
        if (cfg.pauseWithWatch && cfg.controlEnabled && st.beltRunning() && mgr.canControl()) {
            mgr.requestPause();
        }
    }

    // Called by onWorkoutStepComplete (lap key in a workout, or a step's time/distance done).
    // Plain laps (Auto Lap every km) deliberately do NOT count: they would re-send the
    // targets in the middle of a step and undo a change made on the console.
    function onStepComplete() {
        dirty = true;
    }

    function onLinkReady() {
        pendingApply = true;               // (re)connected: send the current targets again
    }

    // Final outcome of a speed or incline command (retries are handled by the manager).
    function onCommandResult(isSpeed, result) {
        var text = "";
        if (result != Ftms.RES_SUCCESS && result != -1) {
            text = (isSpeed ? "Speed " : "Incline ") + Ftms.resultText(result);
        }
        if (isSpeed) {
            speedProblem = text;
        } else {
            inclineProblem = text;
        }
        if (result == Ftms.RES_SUCCESS || result == -1) {
            if (isSpeed) {
                cmdSpeedMs = Sys.getTimer();   // settle window starts when the machine took it
            } else {
                cmdInclineMs = Sys.getTimer();
            }
        } else {
            // final failure (not supported, refused, no control, write failed): the machine
            // will not follow, so a difference from the target is not a manual override
            if (isSpeed) { cmdSpeed = null; } else { cmdIncline = null; }
        }
    }

    // "Target speed/incline changed" from the machine. If it is not what we asked for,
    // someone pressed the console buttons.
    function onMachineTarget(isSpeed, value) {
        if (!timerOn) {
            return;                        // watch paused: console changes are expected
        }
        var now = Sys.getTimer();
        if (isSpeed) {
            if (cmdSpeed != null && !overrideSpeed && now - cmdSpeedMs > 2500 && (value - cmdSpeed).abs() > 0.15) {
                setOverride(true);
            }
        } else {
            if (cmdIncline != null && !overrideIncline && now - cmdInclineMs > 2500 && (value - cmdIncline).abs() > 0.3) {
                setOverride(false);
            }
        }
    }

    // ---------------------------------------------------------------- internals

    hidden function armRemoteStart() {
        remoteStartPending = cfg.remoteStart;
        remoteStartSince = Sys.getTimer();
    }

    hidden function readStep() {
        if (!(Activity has :getCurrentWorkoutStep)) {
            inWorkout = false;
            return;
        }
        var info = null;
        try {
            info = Activity.getCurrentWorkoutStep();
        } catch (e) {
            info = null;
        }
        if (info == null) {
            if (inWorkout) {
                inWorkout = false;         // workout finished or ended: leave the belt as it is
                sig = null;
                stepName = "";
                stepSource = "";
                targetSpeed = null;
                targetIncline = null;
                cmdSpeed = null;
                cmdIncline = null;
            }
            return;
        }
        inWorkout = true;
        var s = signature(info);
        if (dirty || sig == null || !s.equals(sig)) {
            dirty = false;
            sig = s;
            onNewStep(info);
        }
    }

    hidden function signature(info) {
        var s = "" + info.intensity + "|" + info.name + "|" + info.notes;
        var step = info.step;
        if (step instanceof Activity.WorkoutStep) {
            s += "|" + step.targetType + "|" + step.targetValueLow + "|" + step.targetValueHigh
                + "|" + step.durationType + "|" + step.durationValue;
        }
        return s;
    }

    hidden function onNewStep(info) {
        stepName = (info.name != null) ? info.name : "";
        stepSource = "";
        var speed = null;
        var incline = null;
        if (cfg.useNotes) {
            var text = (info.notes != null) ? info.notes : "";
            text = text + " " + stepName;
            speed = Targets.speedFromText(text);
            incline = Targets.inclineFromText(text);
            if (speed != null || incline != null) {
                stepSource = "notes";
            }
        }
        if (speed == null && cfg.usePaceTarget) {
            speed = Targets.speedFromStep(info);
            if (speed != null && stepSource.length() == 0) {
                stepSource = "pace";
            }
        }
        if (incline == null && speed != null && cfg.defaultIncline >= 0.0) {
            incline = cfg.defaultIncline;
        }
        targetSpeed = (speed != null) ? Targets.clampSpeed(speed, st, cfg.maxSpeed) : null;
        targetIncline = (incline != null) ? Targets.clampIncline(incline, st, cfg.maxIncline) : null;

        overrideSpeed = manualSticky;
        overrideIncline = manualSticky;
        cmdSpeed = null;
        cmdIncline = null;
        speedDev = 0;
        inclineDev = 0;
        pendingApply = true;
        mgr.clearControlDenied();
    }

    hidden function apply(now) {
        pendingApply = false;
        if (targetSpeed != null && !overrideSpeed && mgr.speedSupported) {
            if (mgr.requestSpeed(targetSpeed)) {
                cmdSpeed = targetSpeed;
                cmdSpeedMs = now;
                speedDev = 0;
            }
        }
        if (targetIncline != null && !overrideIncline && mgr.inclineSupported) {
            if (mgr.requestIncline(targetIncline)) {
                cmdIncline = targetIncline;
                cmdInclineMs = now;
                inclineDev = 0;
            }
        }
    }

    // Backup override detection from the live data, for consoles that do not report
    // "target changed": the belt has stopped changing, but somewhere other than where we
    // put it. "Stopped changing" makes it independent of how fast the machine ramps.
    hidden function watchForOverride(now) {
        if (cmdSpeed != null && !overrideSpeed && st.hasSpeed && now - cmdSpeedMs > 5000) {
            var steady = (st.speedKmh - lastSpeedSample).abs() < 0.05;
            if (steady && (st.speedKmh - cmdSpeed).abs() > 0.35) {
                speedDev += 1;
                if (speedDev >= 5) {
                    setOverride(true);
                }
            } else {
                speedDev = 0;
            }
        }
        lastSpeedSample = st.speedKmh;
        if (cmdIncline != null && !overrideIncline && st.hasIncline && now - cmdInclineMs > 10000) {
            var steadyIncline = (st.incline - lastInclineSample).abs() < 0.05;
            if (steadyIncline && (st.incline - cmdIncline).abs() > 0.6) {
                inclineDev += 1;
                if (inclineDev >= 8) {     // incline motors move in visible steps: wait longer
                    setOverride(false);
                }
            } else {
                inclineDev = 0;
            }
        }
        lastInclineSample = st.incline;
    }

    hidden function setOverride(isSpeed) {
        if (isSpeed) {
            overrideSpeed = true;
        } else {
            overrideIncline = true;
        }
        if (!cfg.autoResume) {
            manualSticky = true;
        }
    }
}
