// QWebChannel.js — Full inlined from Qt Project (LGPL)
var QWebChannelMessageTypes = {signal:1,propertyUpdate:2,init:3,idle:4,debug:5,invokeMethod:6,connectToSignal:7,disconnectFromSignal:8,setProperty:9,response:10};
var QWebChannel = function(transport, initCallback) {
    if (typeof transport !== "object" || typeof transport.send !== "function") { console.error("QWebChannel: transport missing send()"); return; }
    var channel = this;
    this.transport = transport;
    this.objects = {};
    this.handlers = {};
    this.execCallbacks = {};
    this.execIdCounter = 0;
    this.transport.onmessage = function(msg) {
        var data = JSON.parse(msg.data);
        switch (data.type) {
        case QWebChannelMessageTypes.signal: channel.handleSignal(data); break;
        case QWebChannelMessageTypes.propertyUpdate: channel.handlePropertyUpdate(data); break;
        case QWebChannelMessageTypes.init: channel.handleInit(data); break;
        case QWebChannelMessageTypes.response: channel.handleResponse(data); break;
        case QWebChannelMessageTypes.idle: channel.handleIdle(); break;
        }
    };
    this.send = function(data) { channel.transport.send(JSON.stringify(data)); };
    this.handleIdle = function() { channel.transport.send(JSON.stringify({type: QWebChannelMessageTypes.init})); };
    this.handleInit = function(data) {
        if (!channel.transport.userInitialized) {
            channel.transport.userInitialized = true;
            data.objects.forEach(function(objInfo) {
                channel.objects[objInfo.name] = channel.unwrapQObject(objInfo);
            });
            if (initCallback) initCallback(channel);
        }
    };
    this.handleSignal = function(data) {
        var obj = channel.objects[data.object];
        if (obj && obj._signals && obj._signals[data.signal]) {
            obj._signals[data.signal].forEach(function(h) { h.apply(obj, data.args); });
        }
    };
    this.handlePropertyUpdate = function(data) {
        var obj = channel.objects[data.object];
        if (obj) { obj._properties = obj._properties || {}; data.properties.forEach(function(p) { obj._properties[p.name] = p.value; }); }
    };
    this.handleResponse = function(data) {
        var cb = channel.execCallbacks[data.id];
        if (cb) { delete channel.execCallbacks[data.id]; cb(data.result); }
    };
    this.unwrapQObject = function(objInfo) {
        var obj = {};
        if (objInfo.methods) {
            objInfo.methods.forEach(function(m) {
                if (m.namedReturnValue) {
                    obj[m.name] = function() {
                        var args = Array.prototype.slice.call(arguments);
                        var cb = (typeof args[args.length-1] === 'function') ? args.pop() : null;
                        channel.execCallbacks[++channel.execIdCounter] = cb || function(){};
                        channel.send({type: QWebChannelMessageTypes.invokeMethod, id: channel.execIdCounter, object: objInfo.name, method: m.name, args: args});
                    };
                } else {
                    obj[m.name] = function() {
                        channel.send({type: QWebChannelMessageTypes.invokeMethod, id: 0, object: objInfo.name, method: m.name, args: Array.prototype.slice.call(arguments)});
                    };
                }
            });
        }
        if (objInfo.properties) {
            objInfo.properties.forEach(function(p) {
                Object.defineProperty(obj, p.name, {
                    get: function() { return (obj._properties && obj._properties[p.name]); },
                    set: function(v) { obj._properties = obj._properties || {}; obj._properties[p.name] = v; channel.send({type:QWebChannelMessageTypes.setProperty,object:objInfo.name,property:p.name,value:v}); },
                    enumerable: true, configurable: true
                });
            });
        }
        if (objInfo.signals) {
            obj._signals = {};
            objInfo.signals.forEach(function(s) {
                obj._signals[s.name] = [];
                obj[s.name] = {
                    connect: function(h) { obj._signals[s.name].push(h); channel.send({type:QWebChannelMessageTypes.connectToSignal,object:objInfo.name,signal:s.name}); },
                    disconnect: function(h) { var i = obj._signals[s.name].indexOf(h); if (i>=0) obj._signals[s.name].splice(i,1); channel.send({type:QWebChannelMessageTypes.disconnectFromSignal,object:objInfo.name,signal:s.name}); }
                };
            });
        }
        return obj;
    };
    if (transport.userInitialized === undefined) channel.handleIdle();
};
