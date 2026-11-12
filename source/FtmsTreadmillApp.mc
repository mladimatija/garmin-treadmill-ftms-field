using Toybox.Application;
using Toybox.BluetoothLowEnergy as Ble;
using Toybox.WatchUi as Ui;

// FTMS Treadmill: a Connect IQ data field that connects to a Bluetooth FTMS treadmill
// (tested target: Matrix Endurance with Touch console), shows and records its speed,
// incline and distance, and drives speed and incline from the Garmin workout.
class FtmsTreadmillApp extends Application.AppBase {

    hidden var cfg = null;
    hidden var mgr = null;
    hidden var view = null;

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state) {
    }

    // Activity closed: release the treadmill so the next person (or app) can connect.
    function onStop(state) {
        if (mgr != null) {
            mgr.shutdown();
        }
    }

    function getInitialView() {
        cfg = new FieldSettings();
        cfg.load();
        var st = new TreadmillState();
        mgr = new FtmsManager(st, cfg);
        Ble.setDelegate(mgr);              // the delegate must be set before registering
        mgr.start();
        view = new TreadmillView(mgr, st, cfg);
        return [view];
    }

    function onSettingsChanged() {
        if (cfg != null) {
            cfg.load();
        }
        Ui.requestUpdate();
    }
}
