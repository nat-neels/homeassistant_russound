import asyncio
import re
import logging

# Maintain compat with various 3.x async changes
if hasattr(asyncio, 'ensure_future'):
    ensure_future = asyncio.ensure_future
else:
    ensure_future = getattr(asyncio, 'async')

logger = logging.getLogger(__name__)

_re_response = re.compile(
        r"(?:"
        r"(?:S\[(?P<preset_source>\d+)\].B\[(?P<preset_bank>\d+)\].P\[(?P<preset>\d+)\])|"
        r"(?:S\[(?P<source>\d+)\])|"
        r"(?:C\[(?P<controller>\d+)\].Z\[(?P<zone>\d+)\]))"
        r"\.(?P<variable>\S+)=\"(?P<value>.*)\"")

class CommandException(Exception):
    """ A command sent to the controller caused an error. """
    pass


class UncachedVariable(Exception):
    """ A variable was not found in the cache. """
    pass


class ZoneID:
    """Uniquely identifies a zone

    Russound controllers can be linked together to expand the total zone count.
    Zones are identified by their zone index (1-N) within the controller they
    belong to and the controller index (1-N) within the entire system.
    """
    def __init__(self, zone, controller=1):
        self.zone = int(zone)
        self.controller = int(controller)

    def __str__(self):
        return "%d:%d" % (self.controller, self.zone)

    def __eq__(self, other):
        return hasattr(other, 'zone') and \
                hasattr(other, 'controller') and \
                other.zone == self.zone and \
                other.controller == self.controller

    def __hash__(self):
        return hash(str(self))

    def device_str(self):
        """
        Generate a string that can be used to reference this zone in a RIO
        command
        """
        return "C[%d].Z[%d]" % (self.controller, self.zone)

class PresetID:
    """Uniquely identifies a preset
    Russound presets can be found as part of a source's bank.
    Presets are identified by their preset index [1-6]  within a bank [1-6] on the source they
    belong to.
    """
    def __init__(self, source, bank, preset):
        self.source = int(source)
        self.bank = int(bank)
        self.preset = int(preset)
        
    def __str__(self):
        return "%d:%d:%d" % (self.source, self.bank, self.preset)

    def __eq__(self, other):
        return hasattr(other, 'source') and \
                hasattr(other, 'bank') and \
                hasattr(other, 'preset') and \
                other.source == self.source and \
                other.bank == self.bank and \
                other.preset == self.preset
                
    def __hash__(self):
        return hash(str(self))

    def device_str(self):
        """
        Generate a string that can be used to reference this preset in a RIO
        command
        """
        return "S[%d].B[%d].P[%d]" % (self.source, self.bank, self.preset)
        
class Russound:
    """Manages the RIO connection to a Russound device."""

    def __init__(self, loop, host, port=9621):
        """
        Initialize the Russound object using the event loop, host and port
        provided.
        """
        self._loop = loop
        self._host = host
        self._port = port
        self._ioloop_future = None
        self._cmd_queue = asyncio.Queue()
        self._source_state = {}
        self._zone_state = {}
        self._preset_state = {}
        self._watched_zones = set()
        self._watched_sources = set()
        self._zone_callbacks = []
        self._source_callbacks = []
        self._preset_callbacks = []

    def _retrieve_cached_zone_variable(self, zone_id, name):
        """
        Retrieves the cache state of the named variable for a particular
        zone. If the variable has not been cached then the UncachedVariable
        exception is raised.
        """
        try:
            s = self._zone_state[zone_id][name.lower()]
            logger.debug("Zone Cache retrieve %s.%s = %s",
                         zone_id.device_str(), name, s)
            return s
        except KeyError:
            raise UncachedVariable

    def _store_cached_zone_variable(self, zone_id, name, value):
        """
        Stores the current known value of a zone variable into the cache.
        Calls any zone callbacks.
        """
        zone_state = self._zone_state.setdefault(zone_id, {})
        name = name.lower()
        zone_state[name] = value
        logger.debug("Zone Cache store %s.%s = %s",
                     zone_id.device_str(), name, value)
        for callback in self._zone_callbacks:
            callback(zone_id, name, value)

    def _retrieve_cached_source_variable(self, source_id, name):
        """
        Retrieves the cache state of the named variable for a particular
        source. If the variable has not been cached then the UncachedVariable
        exception is raised.
        """
        try:
            s = self._source_state[source_id][name.lower()]
            logger.debug("Source Cache retrieve S[%d].%s = %s",
                         source_id, name, s)
            return s
        except KeyError:
            raise UncachedVariable

    def _store_cached_source_variable(self, source_id, name, value):
        """
        Stores the current known value of a source variable into the cache.
        Calls any source callbacks.
        """
        source_state = self._source_state.setdefault(source_id, {})
        name = name.lower()
        source_state[name] = value
        logger.debug("Source Cache store S[%d].%s = %s",
                     source_id, name, value)
        for callback in self._source_callbacks:
            callback(source_id, name, value)

    def _retrieve_cached_preset_variable(self, preset_id, name):
        """
        Retrieves the cache state of the named variable for a particular
        preset. If the variable has not been cached then the UncachedVariable
        exception is raised.
        """
        try:
            s = self._preset_state[preset_id][name.lower()]
            logger.debug("Preset Cache retrieve: %s.%s = %s",
                         preset_id.device_str(), name, s)
            return s
        except KeyError:
            raise UncachedVariable

    def _store_cached_preset_variable(self, preset_id, name, value):
        """
        Stores the current known value of a zone variable into the cache.
        Calls any zone callbacks.
        """
        preset_state = self._preset_state.setdefault(preset_id, {})
        name = name.lower()
        preset_state[name] = value
        logger.debug("Preset Cache store %s.%s = %s",
                     preset_id.device_str(), name, value)
        for callback in self._preset_callbacks:
            callback(preset_id, name, value)
            
    def _process_response(self, res):
        s = str(res, 'utf-8').strip()
        ty, payload = s[0], s[2:]     
        if ty == 'E':
            logger.debug("Device responded with error: %s", payload)
            raise CommandException(payload)

        m = _re_response.match(payload)
        if not m:
            return ty, None

        p = m.groupdict()
        if p['source']:
            source_id = int(p['source'])
            self._store_cached_source_variable(
                    source_id, p['variable'], p['value'])
        elif p['zone']:
            zone_id = ZoneID(controller=p['controller'], zone=p['zone'])
            self._store_cached_zone_variable(zone_id,
                                             p['variable'],
                                             p['value'])
        elif p['preset_source']:
            preset_id = PresetID(p['preset_source'], p['preset_bank'], p['preset'])
            self._store_cached_preset_variable(preset_id,
                                             p['variable'],
                                             p['value'])
                                            
        return ty, p['value']

    async def _ioloop(self, reader, writer):
        queue_future = ensure_future(
                self._cmd_queue.get())
        net_future = ensure_future(
                reader.readline())
        try:
            logger.debug("Starting IO loop")
            while True:
                done, pending = await asyncio.wait(
                        [queue_future, net_future],
                        return_when=asyncio.FIRST_COMPLETED)

                if net_future in done:
                    response = net_future.result()
                    try:
                        self._process_response(response)
                    except CommandException:
                        pass
                    net_future = ensure_future(
                            reader.readline())

                if queue_future in done:
                    cmd, future = queue_future.result()
                    cmd += '\r'
                    writer.write(bytearray(cmd, 'utf-8'))
                    await writer.drain()

                    queue_future = ensure_future(
                            self._cmd_queue.get())

                    while True:
                        response = await net_future
                        net_future = ensure_future(
                                reader.readline())
                        try:
                            ty, value = self._process_response(response)
                            if ty == 'S':
                                future.set_result(value)
                                break
                        except CommandException as e:
                            future.set_exception(e)
                            break
            logger.debug("IO loop exited")
        except asyncio.CancelledError:
            logger.debug("IO loop cancelled")
            writer.close()
            queue_future.cancel()
            net_future.cancel()
            raise
        except Exception:
            logger.exception("Unhandled exception in IO loop")
            raise

    async def _send_cmd(self, cmd):
        future = asyncio.Future()
        await self._cmd_queue.put((cmd, future))
        r = await future
        return r

    def add_zone_callback(self, callback):
        """
        Registers a callback to be called whenever a zone variable changes.
        The callback will be passed three arguments: the zone_id, the variable
        name and the variable value.
        """
        self._zone_callbacks.append(callback)

    def remove_zone_callback(self, callback):
        """
        Removes a previously registered zone callback.
        """
        self._zone_callbacks.remove(callback)

    def add_source_callback(self, callback):
        """
        Registers a callback to be called whenever a source variable changes.
        The callback will be passed three arguments: the source_id, the
        variable name and the variable value.
        """
        self._source_callbacks.append(callback)

    def remove_source_callback(self, source_id, callback):
        """
        Removes a previously registered source callback.
        """
        self._source_callbacks.remove(callback)

    def add_preset_callback(self, callback):
        """
        Registers a callback to be called whenever a preset variable changes.
        The callback will be passed three arguments: the preset_id, the variable
        name and the variable value.
        """
        self._preset_callbacks.append(callback)

    def remove_preset_callback(self, callback):
        """
        Removes a previously registered preset callback.
        """
        self._preset_callbacks.remove(callback)
        
    async def connect(self):
        """
        Connect to the controller and start processing responses.
        """
        logger.info("Connecting to %s:%s", self._host, self._port)
        reader, writer = await asyncio.open_connection(
                self._host, self._port)
        self._ioloop_future = ensure_future(
                self._ioloop(reader, writer))
        logger.info("Connected")

    async def close(self):
        """
        Disconnect from the controller.
        """
        logger.info("Closing connection to %s:%s", self._host, self._port)
        self._ioloop_future.cancel()
        try:
            await self._ioloop_future
        except asyncio.CancelledError:
            pass

    async def set_zone_variable(self, zone_id, variable, value):
        """
        Set a zone variable to a new value.
        """
        return self._send_cmd("SET %s.%s=\"%s\"" % (
            zone_id.device_str(), variable, value))

    async def get_zone_variable(self, zone_id, variable):
        """ Retrieve the current value of a zone variable.  If the variable is
        not found in the local cache then the value is requested from the
        controller.  """

        try:
            return self._retrieve_cached_zone_variable(zone_id, variable)
        except UncachedVariable:
            return (await self._send_cmd("GET %s.%s" % (
                zone_id.device_str(), variable)))

    def get_cached_zone_variable(self, zone_id, variable, default=None):
        """ Retrieve the current value of a zone variable from the cache or
        return the default value if the variable is not present. """

        try:
            return self._retrieve_cached_zone_variable(zone_id, variable)
        except UncachedVariable:
            return default

    async def watch_zone(self, zone_id):
        """ Add a zone to the watchlist.
        Zones on the watchlist will push all
        state changes (and those of the source they are currently connected to)
        back to the client """
        r = await self._send_cmd(
                "WATCH %s ON" % (zone_id.device_str(), ))
        self._watched_zones.add(zone_id)
        return r

    async def unwatch_zone(self, zone_id):
        """ Remove a zone from the watchlist. """
        self._watched_zones.remove(zone_id)
        return (await
                self._send_cmd("WATCH %s OFF" % (zone_id.device_str(), )))

    async def send_zone_event(self, zone_id, event_name, *args):
        """ Send an event to a zone. """
        cmd = "EVENT %s!%s %s" % (
                zone_id.device_str(), event_name,
                " ".join(str(x) for x in args))
        return (await self._send_cmd(cmd))

    async def enumerate_zones(self):
        """ Return a list of (zone_id, zone_name) tuples """
        zones = []
        for controller in range(1, 8):
            for zone in range(1, 17):
                zone_id = ZoneID(zone, controller)
                try:
                    name = await self.get_zone_variable(zone_id, 'name')
                    if name:
                        zones.append((zone_id, name))
                except CommandException:
                    break
        return zones

    async def set_source_variable(self, source_id, variable, value):
        """ Change the value of a source variable. """
        source_id = int(source_id)
        return self._send_cmd("SET S[%d].%s=\"%s\"" % (
            source_id, variable, value))

    async def get_source_variable(self, source_id, variable):
        """ Get the current value of a source variable. If the variable is not
        in the cache it will be retrieved from the controller. """

        source_id = int(source_id)
        try:
            return self._retrieve_cached_source_variable(
                    source_id, variable)
        except UncachedVariable:
            return (await self._send_cmd("GET S[%d].%s" % (
                source_id, variable)))

    def get_cached_source_variable(self, source_id, variable, default=None):
        """ Get the cached value of a source variable. If the variable is not
        cached return the default value. """

        source_id = int(source_id)
        try:
            return self._retrieve_cached_source_variable(
                    source_id, variable)
        except UncachedVariable:
            return default

    async def watch_source(self, source_id):
        """ Add a souce to the watchlist. """
        source_id = int(source_id)
        r = await self._send_cmd(
                "WATCH S[%d] ON" % (source_id, ))
        self._watched_sources.add(source_id)
        return r

    async def unwatch_source(self, source_id):
        """ Remove a souce from the watchlist. """
        source_id = int(source_id)
        self._watched_sources.remove(source_id)
        return (await
                self._send_cmd("WATCH S[%d] OFF" % (
                    source_id, )))

    async def get_preset_variable(self, preset_id, variable):
        """ Retrieve the current value of a preset variable.  If the variable is
        not found in the local cache then the value is requested from the
        controller.  """

        try:
            return self._retrieve_cached_preset_variable(preset_id, variable)
        except UncachedVariable:
            return (await self._send_cmd("GET %s.%s" % (
                preset_id.device_str(), variable)))

    async def calc_preset_index(self, bank_id, preset_id):
        """
        Calculates the index of a preset.
        """
        return (((bank_id - 1) * 2) + preset_id)

    async def enumerate_sources(self):
        """ Return a list of (source_id, source_name, source_type) tuples """
        sources = []
        for source_id in range(1, 17):
            try:
                source_name = await self.get_source_variable(source_id, 'name')
                source_type = await self.get_source_variable(source_id, 'type')
                if source_name and source_type:
                    sources.append((source_id, source_name, source_type))
            except CommandException:
                break
        return sources

    async def enumerate_presets(self):
        """ Return a list of (source_id, bank_id, preset_id, index_id, preset_name) tuples """
        banks = []
        for source_id in range(1, 17):
            try:
                source_name = await self.get_source_variable(source_id, 'name')
                source_type = await self.get_source_variable(source_id, 'type')
                if source_name and source_type:
                    if source_type == "RNET AM/FM Tuner (Internal)":
                        for bank_id in range(1, 7):
                            for preset_id in range(1, 7):
                                var_preset_id = PresetID(source_id, bank_id, preset_id)
                                preset_name = await self.get_preset_variable(var_preset_id, 'name')
                                preset_valid = await self.get_preset_variable(var_preset_id, 'valid')
                                if str(preset_valid) == "TRUE":
                                    index_id = await self.calc_preset_index(bank_id, preset_id)
                                    banks.append((source_id, bank_id, preset_id, index_id, preset_name))
            except CommandException:
                break
        return banks
