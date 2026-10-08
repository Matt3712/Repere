// Native-to-map protocol checks. No browser, network, or device is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const layers = [], fits = [];
function layer(kind, coordinates, options = {}) {
  const item = {kind, coordinates, options, removed:false,
    addTo(){ layers.push(this); return this; },
    remove(){ this.removed=true; }, on(){return this;},
    setLatLng(value){this.coordinates=value; return this;},
    setLatLngs(value){this.coordinates=value; return this;},
    setRadius(value){this.options.radius=value; return this;},
    getBounds(){return {center:this.coordinates,radius:this.options.radius};}};
  return item;
}
const map = {setView(){return this;},on(){},invalidateSize(){},fitBounds(bounds){fits.push(bounds);}};
const sandbox = {
  window:{webkit:{messageHandlers:{repere:{postMessage(){}}}}},
  document:{getElementById(){return {style:{}};},createElement(){return {style:{}};},body:{append(){}}},
  setTimeout(){}, ResizeObserver:class{observe(){}},
  L:{map(){return map;},control:{zoom(){return layer('zoom');}},
    tileLayer(){return layer('tiles');},divIcon(value){return value;},
    marker(p,o){return layer('marker',p,o);},polyline(p,o){return layer('polyline',p,o);},
    circle(p,o){return layer('circle',p,o);},latLngBounds(p){return p;}}
};
const html=fs.readFileSync(path.join(__dirname,'Map/index.html'),'utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
vm.runInNewContext(script,sandbox);
const api=sandbox.window;
api.repereSetRandomArea([48.85,2.35],200,true);
const circle=layers.find(l=>l.kind==='circle');
assert.equal(circle.options.radius,200);
assert.equal(fits.length,1);
api.repereSetRandomArea([48.86,2.36],500,false);
assert.equal(layers.filter(l=>l.kind==='circle').length,1,'Changing radius must reuse the circle');
assert.equal(circle.options.radius,500);
assert.equal(fits.length,1,'Progress must not constantly reset the camera');
api.repereTraveler(48.8601,2.3601);
assert.equal(circle.removed,false,'The traveler must preserve the selected area');
api.repereSetRandomArea(null,200,false);
assert.equal(circle.removed,true,'Switching away clears the area');
for(const [center,radius] of [[[91,2],100],[[48,181],100],[[NaN,2],100],[[48,2],9],[[48,2],10001],[[48,2],Infinity]]){
  const count=layers.length;
  api.repereSetRandomArea(center,radius,true);
  assert.equal(layers.length,count,'Invalid areas must not be drawn');
}
api.repereSetRandomArea([0,179.999],10,false);
assert.equal(layers.at(-1).options.radius,10);
api.repereSetRandomArea([0,179.999],10000,false);
assert.equal(layers.at(-1).options.radius,10000);
api.repereTraveler(0,-179.999);
const travelingMarker=layers.find(l=>l.kind==='marker'&&l.options.zIndexOffset===500);
assert.ok(Math.abs(travelingMarker.coordinates[1]-180.001)<1e-9,'Date-line crossing must stay beside the circle');
api.repereSetRandomArea([0,-179.999],10000,false);
api.repereTraveler(0,179.999);
assert.ok(Math.abs(travelingMarker.coordinates[1]+180.001)<1e-9,'Date-line crossing works westward too');
api.repereSetRandomArea([0,179.999],NaN,false);
assert.equal(layers.at(-1).removed,true,'Invalid editing must clear stale bounds');
api.repereRoute([[48,2],[48.1,2.1]],true);
assert.equal(layers.filter(l=>l.kind==='polyline'&&!l.removed).length,2);
api.repereRoute([],false);
assert.equal(layers.filter(l=>l.kind==='polyline'&&!l.removed).length,0);
console.log('Map protocol checks passed: circle bounds, updates, fit, cleanup and route layers.');
