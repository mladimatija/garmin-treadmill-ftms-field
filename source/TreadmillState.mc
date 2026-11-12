using Toybox.System as Sys;

// Live values reported by the treadmill, plus the totals this field accumulates
// while the watch timer is running (session and current lap).
class TreadmillState {

    // ---- live values (Treadmill Data notifications) ----
    var hasSpeed = false;
    var speedKmh = 0.0;
    var hasIncline = false;
    var incline = 0.0;            // %
    var hasMachineDistance = false;
    var hasMachineElevation = false;
    var machineEnergy = -1;       // kcal, the machine's own estimate
    var machineHr = 0;
    var machineElapsed = -1;      // s
    var machineTargetSpeed = -1.0;   // last "target speed changed" status
    var machineTargetIncline = -100.0;
    var lastPacketMs = 0;

    // ---- machine status ----
    var stoppedByUser = false;
    var safetyKey = false;

    // ---- supported ranges (defaults = Matrix Endurance: 0.8-20 km/h, 0-15 % in 0.5 % steps) ----
    var minSpeed = 0.8;
    var maxSpeed = 20.0;
    var speedInc = 0.1;
    var minIncline = 0.0;
    var maxIncline = 15.0;
    var inclineInc = 0.5;

    // ---- totals while the watch timer runs ----
    var timerOn = false;
    var sessDist = 0.0;           // m
    var sessTime = 0.0;           // s with the belt moving
    var sessAscent = 0.0;         // m
    var sessInclineDist = 0.0;    // sum of incline x distance, for a distance-weighted average
    var lapDist = 0.0;
    var lapTime = 0.0;
    var lapAscent = 0.0;
    var lapInclineDist = 0.0;

    hidden var distBase = -1;
    hidden var elevBase = -1.0;

    function initialize() {
    }

    function isFresh() {
        return lastPacketMs != 0 && Sys.getTimer() - lastPacketMs < 5000;
    }

    function beltRunning() {
        return isFresh() && hasSpeed && speedKmh > 0.3 && !safetyKey;
    }

    function setSpeed(v) {
        // A stop/safety-key flag is cleared only when the belt starts moving again, not by
        // the packets that still show speed while the belt slows down after the stop.
        // "Again" = last speed was ~0, or the machine went quiet for a while (some stop sending).
        var gap = lastPacketMs != 0 && Sys.getTimer() - lastPacketMs > 5000;
        if (v > 0.3 && (!hasSpeed || speedKmh <= 0.3 || gap)) {
            stoppedByUser = false;
            safetyKey = false;
        }
        hasSpeed = true;
        speedKmh = v;
        lastPacketMs = Sys.getTimer();
    }

    function setIncline(v) {
        hasIncline = true;
        incline = v;
        lastPacketMs = Sys.getTimer();
    }

    // The machine's own distance counter (m). Only the increase while the watch
    // timer runs is added, so time on the belt before pressing Start is ignored.
    function onMachineDistance(total) {
        hasMachineDistance = true;
        lastPacketMs = Sys.getTimer();
        if (distBase < 0) {
            distBase = total;
            return;
        }
        var d = total - distBase;
        distBase = total;
        if (d > 0 && d < 1000 && timerOn) {   // negative = machine reset, huge = glitch
            addDistance(d.toFloat());
        }
    }

    // The machine's own positive elevation gain counter (m).
    function onMachineElevation(total) {
        hasMachineElevation = true;
        if (elevBase < 0.0) {
            elevBase = total;
            return;
        }
        var d = total - elevBase;
        elevBase = total;
        if (d > 0.0 && d < 100.0 && timerOn) {
            sessAscent += d;
            lapAscent += d;
        }
    }

    // Called once per second from compute().
    function tick(isTimerOn, dt) {
        timerOn = isTimerOn;
        if (!timerOn || !beltRunning() || dt <= 0.0) {
            return;
        }
        sessTime += dt;
        lapTime += dt;
        if (!hasMachineDistance) {
            addDistance(speedKmh / 3.6 * dt);   // machine has no distance field: integrate speed
        }
    }

    hidden function addDistance(d) {
        sessDist += d;
        lapDist += d;
        var inc = hasIncline ? incline : 0.0;
        sessInclineDist += inc * d;
        lapInclineDist += inc * d;
        if (!hasMachineElevation && inc > 0.0) {
            var up = d * inc / 100.0;
            sessAscent += up;
            lapAscent += up;
        }
    }

    function sessAvgSpeed() {
        return sessTime > 0.0 ? sessDist / sessTime * 3.6 : 0.0;
    }

    function lapAvgSpeed() {
        return lapTime > 0.0 ? lapDist / lapTime * 3.6 : 0.0;
    }

    function sessAvgIncline() {
        return sessDist > 0.0 ? sessInclineDist / sessDist : 0.0;
    }

    function lapAvgIncline() {
        return lapDist > 0.0 ? lapInclineDist / lapDist : 0.0;
    }

    function lapReset() {
        lapDist = 0.0;
        lapTime = 0.0;
        lapAscent = 0.0;
        lapInclineDist = 0.0;
    }

    function sessionReset() {
        lapReset();
        sessDist = 0.0;
        sessTime = 0.0;
        sessAscent = 0.0;
        sessInclineDist = 0.0;
    }
}
