using Toybox.BluetoothLowEnergy as Ble;
using Toybox.System as Sys;
using Toybox.Lang;

// Owns the Bluetooth link to the treadmill:
//   register FTMS profile -> scan -> pick the closest treadmill -> connect -> discover
//   -> read features and ranges -> enable notifications/indications -> ready.
// All GATT operations go through one queue, one at a time (Connect IQ allows only one
// outstanding request). Data fields have no Timer, so tick() is driven by compute() ~1 Hz.
class FtmsManager extends Ble.BleDelegate {

    enum {
        LINK_REGISTERING,
        LINK_SCANNING,
        LINK_CONNECTING,
        LINK_DISCOVERING,
        LINK_SETUP,
        LINK_READY,
        LINK_RECONNECTING,
        LINK_ERROR
    }

    enum {
        OP_READ,
        OP_CCCD,
        OP_CP
    }

    var link = LINK_REGISTERING;
    var deviceName = "";
    var problem = "";            // last thing that went wrong, for the display
    var follower = null;         // WorkoutFollower, set by the view

    // what the machine can do (assumed until it tells us otherwise)
    var profileLevel = 0;        // 3 = everything, 2 = data + control, 1 = data only
    var machineFeatures = 0;
    var targetFeatures = 0;
    var speedSupported = true;
    var inclineSupported = true;
    var hasControl = false;
    var controlDenied = false;

    hidden var st;
    hidden var cfg;
    hidden var svcUuid;
    hidden var featUuid;
    hidden var dataUuid;
    hidden var speedRangeUuid;
    hidden var inclRangeUuid;
    hidden var cpUuid;
    hidden var statusUuid;

    hidden var device = null;
    hidden var linkSince = 0;
    hidden var scanStart = 0;
    hidden var scanning = false;
    hidden var candidates = [];  // [scanResult, rssi, name]

    hidden var queue = [];       // [type, uuid, payload, tries]
    hidden var busy = false;
    hidden var busyOp = null;
    hidden var busySince = 0;
    hidden var cpWaiting = -1;   // op code waiting for its Control Point response
    hidden var cpSince = 0;
    hidden var noIndications = false;
    hidden var controlRequested = false;
    hidden var notPermittedRetries = 0;
    hidden var resumeTried = false;
    hidden var lastSpeedCmd = null;
    hidden var lastInclineCmd = null;

    function initialize(state, settings) {
        BleDelegate.initialize();
        st = state;
        cfg = settings;
        svcUuid = Ble.stringToUuid("00001826-0000-1000-8000-00805f9b34fb");
        featUuid = Ble.stringToUuid("00002acc-0000-1000-8000-00805f9b34fb");
        dataUuid = Ble.stringToUuid("00002acd-0000-1000-8000-00805f9b34fb");
        speedRangeUuid = Ble.stringToUuid("00002ad4-0000-1000-8000-00805f9b34fb");
        inclRangeUuid = Ble.stringToUuid("00002ad5-0000-1000-8000-00805f9b34fb");
        cpUuid = Ble.stringToUuid("00002ad9-0000-1000-8000-00805f9b34fb");
        statusUuid = Ble.stringToUuid("00002ada-0000-1000-8000-00805f9b34fb");
    }

    // ------------------------------------------------------------------ public

    function start() {
        setLink(LINK_REGISTERING);
        registerProfile(3);
    }

    function shutdown() {
        setScan(false);
        unpairAll();
        device = null;
    }

    function isReady() {
        return link == LINK_READY;
    }

    function isError() {
        return link == LINK_ERROR;
    }

    // The machine can be driven at all (control point present, speed or incline accepted).
    function controllable() {
        return profileLevel >= 2 && (speedSupported || inclineSupported) && getChar(cpUuid) != null;
    }

    // ... and right now.
    function canControl() {
        return link == LINK_READY && cfg.controlEnabled && !controlDenied && controllable();
    }

    // Called by the follower at every new workout step: a refusal is worth one more try.
    function clearControlDenied() {
        controlDenied = false;
        notPermittedRetries = 0;
        resumeTried = false;
    }

    function requestSpeed(kmh) {
        if (!canControl() || !speedSupported) {
            return false;
        }
        lastSpeedCmd = kmh;
        ensureControl();
        enqueueCp(Ftms.encodeSpeed(kmh, cfg.mphMode), true);
        pump();
        return true;
    }

    function requestIncline(pct) {
        if (!canControl() || !inclineSupported) {
            return false;
        }
        lastInclineCmd = pct;
        ensureControl();
        enqueueCp(Ftms.encodeIncline(pct), true);
        pump();
        return true;
    }

    function requestStartResume() {
        if (!canControl()) {
            return false;
        }
        ensureControl();
        enqueueCp([0x07]b, true);
        pump();
        return true;
    }

    function requestPause() {
        if (!canControl()) {
            return false;
        }
        ensureControl();
        enqueueCp([0x08, 0x02]b, true);
        pump();
        return true;
    }

    function statusText() {
        if (link == LINK_REGISTERING) { return "Starting BLE"; }
        if (link == LINK_ERROR) { return problem; }
        if (link == LINK_SCANNING) {
            if (candidates.size() == 0) { return "Searching..."; }
            if (problem.length() > 0) { return problem; }
            return "Found " + candidates.size();
        }
        if (link == LINK_CONNECTING) { return "Connecting"; }
        if (link == LINK_DISCOVERING || link == LINK_SETUP) { return "Setting up"; }
        if (link == LINK_RECONNECTING) { return "Reconnecting"; }
        return deviceName.length() > 0 ? deviceName : "Treadmill";
    }

    // Driven from compute(), about once per second.
    function tick() {
        var now = Sys.getTimer();
        if (link == LINK_REGISTERING) {
            if (now - linkSince > 6000) {
                startScan();       // no onProfileRegister callback on some firmware: just try
            }
        } else if (link == LINK_SCANNING) {
            if (candidates.size() > 0 && now - scanStart > 4000) {
                connectBest();
            } else if (now - scanStart > 30000) {
                setScan(false);    // restart the scan now and then, results go stale
                startScan();
            }
        } else if (link == LINK_CONNECTING) {
            if (device != null && device.isConnected()) {
                onLinkUp();
            } else if (now - linkSince > 15000) {
                dropAndRescan("Connect timeout");
            }
        } else if (link == LINK_DISCOVERING) {
            discover(now);
        } else if (link == LINK_SETUP || link == LINK_READY) {
            if (device == null || !device.isConnected()) {
                onLinkDown();
            } else {
                pump();
            }
        } else if (link == LINK_RECONNECTING) {
            if (device != null && device.isConnected()) {
                onLinkUp();
            } else if (now - linkSince > 10000) {
                dropAndRescan("Lost link");
            }
        }
    }

    // ------------------------------------------------------------------ link

    hidden function setLink(l) {
        link = l;
        linkSince = Sys.getTimer();
    }

    hidden function registerProfile(level) {
        profileLevel = level;
        linkSince = Sys.getTimer();        // restart the registration timeout for each attempt
        var chars;
        if (level == 3) {
            chars = [
                {:uuid => featUuid},
                {:uuid => dataUuid, :descriptors => [Ble.cccdUuid()]},
                {:uuid => speedRangeUuid},
                {:uuid => inclRangeUuid},
                {:uuid => cpUuid, :descriptors => [Ble.cccdUuid()]},
                {:uuid => statusUuid, :descriptors => [Ble.cccdUuid()]}
            ];
        } else if (level == 2) {
            chars = [
                {:uuid => dataUuid, :descriptors => [Ble.cccdUuid()]},
                {:uuid => cpUuid, :descriptors => [Ble.cccdUuid()]},
                {:uuid => statusUuid, :descriptors => [Ble.cccdUuid()]}
            ];
        } else {
            chars = [
                {:uuid => dataUuid, :descriptors => [Ble.cccdUuid()]}
            ];
        }
        try {
            doRegister({:uuid => svcUuid, :characteristics => chars});
        } catch (e) {
            profileFailed();
        }
    }

    // Untyped parameter on purpose: keeps the type checker away from the profile dictionary.
    hidden function doRegister(profile) {
        Ble.registerProfile(profile);
    }

    hidden function profileFailed() {
        if (profileLevel > 1) {
            registerProfile(profileLevel - 1);    // try a smaller profile
        } else {
            profileLevel = 0;
            problem = "BLE busy";
            setLink(LINK_ERROR);
        }
    }

    hidden function startScan() {
        candidates = [];
        scanStart = Sys.getTimer();
        device = null;
        setLink(LINK_SCANNING);
        setScan(true);
    }

    hidden function setScan(on) {
        if (on == scanning) {
            return;
        }
        try {
            Ble.setScanState(on ? Ble.SCAN_STATE_SCANNING : Ble.SCAN_STATE_OFF);
            scanning = on;
        } catch (e) {
            problem = "Scan error";
        }
    }

    hidden function advertisesFtms(r) {
        var it = r.getServiceUuids();
        if (it == null) {
            return false;
        }
        for (var u = it.next(); u != null; u = it.next()) {
            if (u.equals(svcUuid)) {
                return true;
            }
        }
        return false;
    }

    hidden function connectBest() {
        var best = null;
        var bestRssi = -999;
        var bestName = "";
        for (var i = 0; i < candidates.size(); i++) {
            if (candidates[i][1] > bestRssi) {
                best = candidates[i][0];
                bestRssi = candidates[i][1];
                bestName = candidates[i][2];
            }
        }
        if (best == null) {
            return;
        }
        if (bestRssi < cfg.rssiMin) {
            problem = "Move closer";
            return;                        // keep scanning
        }
        setScan(false);
        unpairAll();
        try {
            device = Ble.pairDevice(best);
        } catch (e) {
            device = null;
        }
        if (device == null) {
            problem = "Pair failed";
            startScan();
            return;
        }
        problem = "";
        deviceName = bestName;
        setLink(LINK_CONNECTING);
        if (device.isConnected()) {        // onConnectedStateChanged is not always called
            onLinkUp();
        }
    }

    hidden function onLinkUp() {
        resetSession();
        setLink(LINK_DISCOVERING);
        discover(Sys.getTimer());
    }

    hidden function onLinkDown() {
        resetSession();
        setLink(LINK_RECONNECTING);       // the system reconnects a paired device by itself
    }

    hidden function dropAndRescan(reason) {
        problem = reason;
        resetSession();
        unpairAll();
        startScan();
    }

    hidden function discover(now) {
        var svc = null;
        try {
            if (device != null) {
                svc = device.getService(svcUuid);
            }
        } catch (e) {
            svc = null;
        }
        // The service object can appear before its characteristics are discovered:
        // wait for Treadmill Data, otherwise every setup step would be skipped.
        var dataChar = null;
        if (svc != null) {
            try {
                dataChar = svc.getCharacteristic(dataUuid);
            } catch (e) {
                dataChar = null;
            }
        }
        if (dataChar != null) {
            buildSetupQueue();
            setLink(LINK_SETUP);
            pump();
        } else if (now - linkSince > 10000) {
            dropAndRescan("No FTMS");
        }
    }

    hidden function resetSession() {
        queue = [];
        busy = false;
        busyOp = null;
        cpWaiting = -1;
        hasControl = false;
        controlRequested = false;
        notPermittedRetries = 0;
        resumeTried = false;
    }

    hidden function unpairAll() {
        var list = [];
        try {
            var it = Ble.getPairedDevices();
            for (var d = it.next(); d != null; d = it.next()) {
                list.add(d);
            }
        } catch (e) {
        }
        for (var i = 0; i < list.size(); i++) {
            try {
                Ble.unpairDevice(list[i]);
            } catch (e) {
            }
        }
    }

    hidden function buildSetupQueue() {
        queue = [];
        noIndications = false;
        if (profileLevel == 3) {
            queue.add([OP_READ, featUuid, null, 0]);
            queue.add([OP_READ, speedRangeUuid, null, 0]);
            queue.add([OP_READ, inclRangeUuid, null, 0]);
        }
        queue.add([OP_CCCD, dataUuid, [0x01, 0x00]b, 0]);          // notify
        if (profileLevel >= 2) {
            queue.add([OP_CCCD, statusUuid, [0x01, 0x00]b, 0]);    // notify
            if (cfg.controlEnabled) {
                queue.add([OP_CCCD, cpUuid, [0x02, 0x00]b, 0]);    // indicate
            }
        }
    }

    // ------------------------------------------------------------------ GATT queue

    hidden function getChar(uuid) {
        if (device == null) {
            return null;
        }
        try {
            var svc = device.getService(svcUuid);
            if (svc == null) {
                return null;
            }
            return svc.getCharacteristic(uuid);
        } catch (e) {
            return null;
        }
    }

    hidden function ensureControl() {
        if (hasControl || controlRequested) {
            return;
        }
        controlRequested = true;
        var q = [[OP_CP, cpUuid, [0x00]b, 0]];  // goes before any queued command
        q.addAll(queue);
        queue = q;
    }

    // coalesce: replace an already queued command with the same op code (latest value wins)
    hidden function enqueueCp(bytes, coalesce) {
        if (coalesce) {
            for (var i = 0; i < queue.size(); i++) {
                var op = queue[i];
                if (op[0] == OP_CP && op[2][0] == bytes[0]) {
                    op[2] = bytes;
                    return;
                }
            }
        }
        queue.add([OP_CP, cpUuid, bytes, 0]);
    }

    // Put a command at the head of the queue, behind a pending control request if there is one.
    hidden function enqueueCpFirst(bytes) {
        var at = 0;
        if (queue.size() > 0 && queue[0][0] == OP_CP && queue[0][2][0] == Ftms.CP_REQUEST_CONTROL) {
            at = 1;
        }
        var q = queue.slice(0, at);
        q.add([OP_CP, cpUuid, bytes, 0]);
        q.addAll(queue.slice(at, null));
        queue = q;
    }

    hidden function purgeCommands() {
        var q = [];
        for (var i = 0; i < queue.size(); i++) {
            if (queue[i][0] != OP_CP) {
                q.add(queue[i]);
            }
        }
        queue = q;
    }

    hidden function pump() {
        var now = Sys.getTimer();
        if (busy) {
            if (now - busySince < 5000) {
                return;
            }
            busy = false;                  // the stack never answered; move on
            busyOp = null;
        }
        if (cpWaiting >= 0) {
            if (now - cpSince < 4000) {
                return;
            }
            var op = cpWaiting;
            cpWaiting = -1;
            noIndications = true;          // this machine does not send responses
            onCpResult(op, -1);
        }
        while (queue.size() > 0) {
            var next = queue[0];
            queue = queue.slice(1, null);
            var r = startOp(next);
            if (r == 1) {
                return;                    // started, wait for its callback
            }
            if (r == 2) {
                return;                    // put back, try again next tick
            }
        }
        if (link == LINK_SETUP) {
            problem = "";
            setLink(LINK_READY);
            if (follower != null) {
                follower.onLinkReady();
            }
        }
    }

    // 0 = skipped, 1 = started, 2 = re-queued after an error
    hidden function startOp(op) {
        var ch = getChar(op[1]);
        if (ch == null) {
            return 0;
        }
        try {
            if (op[0] == OP_READ) {
                ch.requestRead();
            } else if (op[0] == OP_CCCD) {
                var d = ch.getDescriptor(Ble.cccdUuid());
                if (d == null) {
                    return 0;
                }
                d.requestWrite(op[2]);
            } else {
                ch.requestWrite(op[2], {:writeType => Ble.WRITE_TYPE_WITH_RESPONSE});
                if (!noIndications) {
                    cpWaiting = op[2][0];
                    cpSince = Sys.getTimer();
                }
            }
        } catch (e) {
            cpWaiting = -1;
            op[3] = op[3] + 1;
            if (op[3] < 3) {
                var q = [op];
                q.addAll(queue);
                queue = q;
                return 2;
            }
            if (op[0] == OP_CP) {
                onCpResult(op[2][0], -2);
            }
            return 0;
        }
        busy = true;
        busyOp = op;
        busySince = Sys.getTimer();
        return 1;
    }

    // ------------------------------------------------------------------ control point

    hidden function onCpResult(op, result) {
        if (op == cpWaiting) {
            cpWaiting = -1;
        }
        if (op == Ftms.CP_REQUEST_CONTROL) {
            controlRequested = false;
            if (result == Ftms.RES_SUCCESS || result == -1) {
                hasControl = true;         // no reply at all: try the commands anyway
            } else {
                hasControl = false;
                controlDenied = true;
                problem = (result == -2) ? "Control write failed" : "Control refused";
                purgeCommands();
            }
            return;
        }
        if (op != Ftms.CP_SET_SPEED && op != Ftms.CP_SET_INCLINE) {
            return;
        }
        var isSpeed = (op == Ftms.CP_SET_SPEED);
        var retrying = false;
        if (result == Ftms.RES_SUCCESS) {
            notPermittedRetries = 0;
            problem = "";
        } else if (result == Ftms.RES_NOT_SUPPORTED) {
            if (isSpeed) { speedSupported = false; } else { inclineSupported = false; }
            problem = (isSpeed ? "Speed " : "Incline ") + "not supported";
        } else if (result == Ftms.RES_NOT_PERMITTED && notPermittedRetries < 1) {
            notPermittedRetries += 1;      // control was taken away: ask again, resend once
            hasControl = false;
            controlRequested = false;
            resend(isSpeed);
            retrying = true;
        } else if (result == Ftms.RES_FAILED && !resumeTried && st.beltRunning()) {
            // Some machines only accept targets after an FTMS "start". The belt is already
            // moving, so Start/Resume does not change anything physically.
            resumeTried = true;
            ensureControl();
            enqueueCpFirst([0x07]b);       // before any other queued command (e.g. the incline)
            resend(isSpeed);
            retrying = true;
        } else if (result != -1) {
            problem = (isSpeed ? "Speed " : "Incline ") + Ftms.resultText(result);
        }
        if (follower != null && !retrying) {
            follower.onCommandResult(isSpeed, result);
        }
    }

    hidden function resend(isSpeed) {
        ensureControl();
        if (isSpeed && lastSpeedCmd != null) {
            enqueueCp(Ftms.encodeSpeed(lastSpeedCmd, cfg.mphMode), true);
        } else if (!isSpeed && lastInclineCmd != null) {
            enqueueCp(Ftms.encodeIncline(lastInclineCmd), true);
        }
    }

    hidden function onStatus(v) {
        if (v.size() < 1) {
            return;
        }
        var op = v[0];
        if (op == Ftms.ST_RESET) {
            hasControl = false;
            controlRequested = false;
        } else if (op == Ftms.ST_STOPPED_BY_USER) {
            st.stoppedByUser = true;
        } else if (op == Ftms.ST_SAFETY_KEY) {
            st.safetyKey = true;
            st.stoppedByUser = true;
        } else if (op == Ftms.ST_STARTED_BY_USER) {
            st.stoppedByUser = false;
            st.safetyKey = false;
        } else if (op == Ftms.ST_TARGET_SPEED && v.size() >= 3) {
            var s = Ftms.u16(v, 1) / 100.0;
            if (cfg.mphMode) {
                s = s * Ftms.MPH_TO_KMH;
            }
            st.machineTargetSpeed = s;
            if (follower != null) {
                follower.onMachineTarget(true, s);
            }
        } else if (op == Ftms.ST_TARGET_INCLINE && v.size() >= 3) {
            var p = Ftms.s16(v, 1) / 10.0;
            st.machineTargetIncline = p;
            if (follower != null) {
                follower.onMachineTarget(false, p);
            }
        } else if (op == Ftms.ST_CONTROL_LOST) {
            hasControl = false;
            controlRequested = false;
            problem = "Control lost";
        }
    }

    // ------------------------------------------------------------------ BleDelegate

    function onProfileRegister(uuid, status) {
        if (link != LINK_REGISTERING) {
            return;                        // late answer, already moved on
        }
        if (status == Ble.STATUS_SUCCESS) {
            startScan();
        } else {
            profileFailed();
        }
    }

    function onScanStateChange(scanState, status) {
        scanning = (scanState == Ble.SCAN_STATE_SCANNING);
    }

    function onScanResults(scanResults) {
        if (link != LINK_SCANNING) {
            return;
        }
        collect(scanResults);
    }

    // Untyped parameter on purpose (the iterator's items are plain Objects to the type checker).
    hidden function collect(scanResults) {
        var filter = cfg.nameFilter.toLower();
        for (var r = scanResults.next(); r != null; r = scanResults.next()) {
            var name = r.getDeviceName();
            if (name == null) {
                name = "";
            }
            var nameOk = filter.length() > 0 && name.toLower().find(filter) != null;
            if (filter.length() > 0 && !nameOk) {
                continue;
            }
            if (!nameOk && !advertisesFtms(r)) {
                continue;
            }
            var known = false;
            for (var i = 0; i < candidates.size(); i++) {
                if (r.isSameDevice(candidates[i][0])) {
                    candidates[i] = [r, r.getRssi(), name];
                    known = true;
                    break;
                }
            }
            if (!known && candidates.size() < 8) {
                candidates.add([r, r.getRssi(), name]);
            }
        }
    }

    function onConnectedStateChanged(dev, state) {
        if (state == Ble.CONNECTION_STATE_CONNECTED) {
            if (link == LINK_CONNECTING || link == LINK_RECONNECTING) {
                device = dev;
                onLinkUp();
            }
        } else if (link == LINK_DISCOVERING || link == LINK_SETUP || link == LINK_READY) {
            onLinkDown();
        }
    }

    function onCharacteristicRead(ch, status, value) {
        busy = false;
        busyOp = null;
        if (status == Ble.STATUS_SUCCESS && value != null) {
            var u = ch.getUuid();
            if (u.equals(featUuid)) {
                var f = Ftms.parseFeature(value);
                if (f != null) {
                    machineFeatures = f[0];
                    targetFeatures = f[1];
                    // Only trust "not supported" if the machine reports any target bits at all.
                    if (targetFeatures != 0) {
                        speedSupported = (targetFeatures & 0x01) != 0;
                        inclineSupported = (targetFeatures & 0x02) != 0;
                    }
                }
            } else if (u.equals(speedRangeUuid)) {
                Ftms.parseSpeedRange(value, st, cfg.mphMode);
            } else if (u.equals(inclRangeUuid)) {
                Ftms.parseInclineRange(value, st);
            }
        }
        pump();
    }

    function onCharacteristicWrite(ch, status) {
        var wasCp = busyOp != null && busyOp[0] == OP_CP;
        var op = wasCp ? busyOp[2][0] : -1;
        busy = false;
        busyOp = null;
        if (wasCp && status != Ble.STATUS_SUCCESS) {
            onCpResult(op, -2);
        } else if (wasCp && noIndications) {
            onCpResult(op, Ftms.RES_SUCCESS);   // best we can know without responses
        }
        pump();
    }

    function onDescriptorWrite(desc, status) {
        busy = false;
        busyOp = null;
        pump();
    }

    function onDescriptorRead(desc, status, value) {
        busy = false;
        busyOp = null;
        pump();
    }

    function onCharacteristicChanged(ch, value) {
        if (value == null) {
            return;
        }
        var u = ch.getUuid();
        if (u.equals(dataUuid)) {
            Ftms.parseTreadmillData(value, st, cfg.mphMode);
        } else if (u.equals(cpUuid)) {
            if (value.size() >= 3 && value[0] == Ftms.CP_RESPONSE) {
                onCpResult(value[1], value[2]);
                pump();
            }
        } else if (u.equals(statusUuid)) {
            onStatus(value);
        }
    }
}
