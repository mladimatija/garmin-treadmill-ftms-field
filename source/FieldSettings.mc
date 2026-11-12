using Toybox.Application;
using Toybox.Lang;

// User settings (Garmin Connect app > device > Connect IQ apps > FTMS Treadmill > Settings).
class FieldSettings {

    var nameFilter = "";        // only connect to treadmills whose Bluetooth name contains this
    var rssiMin = -90;          // ignore treadmills weaker than this (dBm)
    var controlEnabled = true;  // false = read-only, never send commands
    var useNotes = true;        // read "12.0 km/h, 1% incline" from step notes
    var usePaceTarget = true;   // otherwise use the middle of the step's pace target
    var defaultIncline = 1.0;   // incline for steps that set a speed but no incline; < 0 = leave it
    var maxSpeed = 16.0;        // never command more than this (km/h)
    var maxIncline = 8.0;       // never command more than this (%)
    var autoResume = true;      // after a console override, take control again at the next step
    var remoteStart = false;    // start the belt from the watch Start button (off for safety)
    var pauseWithWatch = false; // pausing the watch pauses the belt
    var mphMode = false;        // treadmill sends/expects mph instead of km/h (rare)
    var recordFit = true;       // write treadmill speed/incline/distance into the activity
    var mainValue = 0;          // 0 = speed, 1 = pace

    function initialize() {
    }

    function load() {
        nameFilter = readString("NameFilter", "");
        rssiMin = readNumber("RssiMin", -90.0).toNumber();
        controlEnabled = readBool("ControlEnabled", true);
        useNotes = readBool("UseNotes", true);
        usePaceTarget = readBool("UsePaceTarget", true);
        defaultIncline = readNumber("DefaultIncline", 1.0);
        maxSpeed = readNumber("MaxSpeed", 16.0);
        maxIncline = readNumber("MaxIncline", 8.0);
        autoResume = readBool("AutoResume", true);
        remoteStart = readBool("RemoteStart", false);
        pauseWithWatch = readBool("PauseWithWatch", false);
        mphMode = readBool("MphMode", false);
        recordFit = readBool("RecordFit", true);
        mainValue = readNumber("MainValue", 0.0).toNumber();
        if (maxSpeed < 1.0) { maxSpeed = 1.0; }
        if (maxSpeed > 25.0) { maxSpeed = 25.0; }
        if (maxIncline < 0.0) { maxIncline = 0.0; }
        if (maxIncline > 20.0) { maxIncline = 20.0; }
    }

    hidden function readValue(key) {
        try {
            return Application.Properties.getValue(key);
        } catch (e) {
            return null;
        }
    }

    hidden function readBool(key, dflt) {
        var v = readValue(key);
        return (v instanceof Lang.Boolean) ? v : dflt;
    }

    hidden function readNumber(key, dflt) {
        var v = readValue(key);
        if (v instanceof Lang.Number || v instanceof Lang.Float || v instanceof Lang.Double || v instanceof Lang.Long) {
            return v.toFloat();
        }
        return dflt;
    }

    hidden function readString(key, dflt) {
        var v = readValue(key);
        return (v instanceof Lang.String) ? v : dflt;
    }
}
