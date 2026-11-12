using Toybox.FitContributor as Fit;

// Treadmill values written into the activity file as Connect IQ developer fields.
// Garmin Connect shows them as extra charts, lap columns and summary values
// (labels come from resources/fitcontributions/fitcontributions.xml; ids must match).
// Note: developer fields cannot replace the watch's own distance/pace; at the end of
// a treadmill run the watch offers "Calibrate & Save" where the treadmill distance
// shown by this field can be typed in.
class FitRecorder {

    hidden var fSpeed;
    hidden var fIncline;
    hidden var fTarget;
    hidden var fLapDist;
    hidden var fLapSpeed;
    hidden var fLapAscent;
    hidden var fLapIncline;
    hidden var fDist;
    hidden var fSpeedAvg;
    hidden var fAscent;
    hidden var fInclineAvg;

    function initialize(df) {
        fSpeed = df.createField("tm_speed", 0, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_RECORD, :units => "km/h"});
        fIncline = df.createField("tm_incline", 1, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_RECORD, :units => "%"});
        fTarget = df.createField("tm_target_speed", 2, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_RECORD, :units => "km/h"});

        fLapDist = df.createField("tm_lap_distance", 3, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_LAP, :units => "km"});
        fLapSpeed = df.createField("tm_lap_speed", 4, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_LAP, :units => "km/h"});
        fLapAscent = df.createField("tm_lap_ascent", 5, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_LAP, :units => "m"});
        fLapIncline = df.createField("tm_lap_incline", 6, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_LAP, :units => "%"});

        fDist = df.createField("tm_distance", 7, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_SESSION, :units => "km"});
        fSpeedAvg = df.createField("tm_avg_speed", 8, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_SESSION, :units => "km/h"});
        fAscent = df.createField("tm_ascent", 9, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_SESSION, :units => "m"});
        fInclineAvg = df.createField("tm_avg_incline", 10, Fit.DATA_TYPE_FLOAT,
            {:mesgType => Fit.MESG_TYPE_SESSION, :units => "%"});
    }

    function update(st, follower) {
        var fresh = st.isFresh();
        fSpeed.setData(fresh && st.hasSpeed ? st.speedKmh.toFloat() : 0.0);
        fIncline.setData(fresh && st.hasIncline ? st.incline.toFloat() : 0.0);
        fTarget.setData(follower.targetSpeed != null ? follower.targetSpeed.toFloat() : 0.0);

        fLapDist.setData((st.lapDist / 1000.0).toFloat());
        fLapSpeed.setData(st.lapAvgSpeed().toFloat());
        fLapAscent.setData(st.lapAscent.toFloat());
        fLapIncline.setData(st.lapAvgIncline().toFloat());

        fDist.setData((st.sessDist / 1000.0).toFloat());
        fSpeedAvg.setData(st.sessAvgSpeed().toFloat());
        fAscent.setData(st.sessAscent.toFloat());
        fInclineAvg.setData(st.sessAvgIncline().toFloat());
    }
}
