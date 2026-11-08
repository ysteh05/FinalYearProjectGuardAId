const { test } = require('node:test');
const assert = require('node:assert/strict');
const { eventAt, alarmsBetween, containRect, weaponColors, violenceColors, livePosition, markPlaybackError } = require('../app/static/monitor.js');

test('live position follows CCTV arrival minus two seconds, not model or player progress',()=>{
  assert.equal(livePosition(.5,20),0);
  assert.equal(livePosition(10,20),8);
  assert.equal(livePosition(13.5,20),11.5);
  assert.equal(livePosition(22,20),20);
  assert.equal(livePosition(1.9,20),0);
  assert.equal(livePosition(2.1,.1),.1);
  assert.equal(livePosition(10,20,1),9);
});
test('boxes hold exactly 1/FPS, clear on next negative, never show future/stale detections', () => {
  for (const interval of [.5, .25]) {
    const events = [{detector:'weapon', sourceSeconds:2, validUntilSeconds:2+interval, boxes:[1]}];
    assert.equal(eventAt(events,'weapon',1.999),null);
    assert.equal(eventAt(events,'weapon',2).boxes.length,1);
    assert.equal(eventAt(events,'weapon',2+interval-.0001).boxes.length,1);
    assert.equal(eventAt(events,'weapon',2+interval),null);
    events.push({detector:'weapon',sourceSeconds:2+interval,validUntilSeconds:2+2*interval,boxes:[]});
    assert.deepEqual(eventAt(events,'weapon',2+interval).boxes,[]);
  }
});
test('first violence score belongs to window END, and old score expires', () => {
  const events=[{detector:'violence',sourceSeconds:4,validUntilSeconds:6,probability:.9}];
  assert.equal(eventAt(events,'violence',3.999),null);
  assert.equal(eventAt(events,'violence',4).probability,.9);
  assert.equal(eventAt(events,'violence',6),null);
});
test('alarm only when crossing a confirmed event, never on polling, stale data or seeks backwards', () => {
  const events=[{sourceSeconds:1,validUntilSeconds:1.5,alarm:false},
    {sourceSeconds:2,validUntilSeconds:2.5,alarm:true,alarmLevel:1}];
  assert.equal(alarmsBetween(events,0,1).length,0);
  assert.equal(alarmsBetween(events,1.9,2.1).length,1);
  assert.equal(alarmsBetween(events,2.1,2.2).length,0);
  assert.equal(alarmsBetween(events,2.1,1).length,0);
  assert.equal(alarmsBetween(events,0,3).length,0);
});
test('letterbox coordinates cover 16:9, 9:16 and square without stretching', () => {
  assert.deepEqual(containRect(1920,1080,160,90),{x:0,y:0,width:160,height:90});
  const portrait=containRect(1080,1920,160,90);
  assert.equal(portrait.height,90);assert.equal(portrait.width,50.625);assert.equal(portrait.x,54.6875);
  assert.deepEqual(containRect(100,100,160,90),{x:35,y:0,width:90,height:90});
});
test('requested independent color palettes', () => {
  assert.deepEqual(weaponColors.slice(1),['#41df83','#ffe052','#ff4658']);
  assert.deepEqual(violenceColors.slice(1),['#ffe052','#ff9b38','#ff4658']);
});
test('a browser video decode error is shown as a playback issue, not a stopped analysis',()=>{
  const session={status:'running',mode:'live',playerError:false,needsGesture:false};
  const label=markPlaybackError(session);
  assert.equal(session.playerError,true);
  assert.equal(session.status,'running');
  assert.equal(session.mode,'live');
  assert.equal(session.needsGesture,true);
  assert.match(label,/Playback unavailable/);
  assert.match(label,/analysis continues in background/);
});
