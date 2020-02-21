from datetime import datetime, timedelta
import time
import math
import numpy as np
import sys, os, platform
import socket as s
import signal
from obspy import UTCDateTime
from obspy.core.stream import Stream
from obspy import read_inventory
from obspy.geodetics.flinnengdahl import FlinnEngdahl
from obspy.core.trace import Trace
from rsudp import printM
from requests.exceptions import HTTPError

initd, sockopen = False, False
qsize = 2048 			# max queue size
port = 8888				# default listening port
to = 10					# socket test timeout
firstaddr = ''			# the first address data is received from
inv = False				# station inventory
region = False
producer = False 		# flag for producer status
stn = 'Z0000'			# station name
net = 'AM'				# network (this will always be AM)
chns = []				# list of channels
numchns = 0

tf = None				# transmission frequency in ms
tr = None				# transmission rate in packets per second
sps = None				# samples per second

# get an IP to report to the user
# from https://stackoverflow.com/questions/166506/finding-local-ip-addresses-using-pythons-stdlib
def get_ip():
	'''
	Return a reliable network IP to report to the user when there is no data received.
	This helps the user set their Raspberry Shake's datacast streams to point to the correct location
	if the library raises a "no data received" error.
	Solution adapted from
	`this stackoverflow answer <https://stackoverflow.com/questions/166506/finding-local-ip-addresses-using-pythons-stdlib>`_.

	:rtype: str
	:return: The network IP of the machine that this program is running on
	'''

	testsock = s.socket(s.AF_INET, s.SOCK_DGRAM)
	try:
		# doesn't even have to be reachable
		testsock.connect(('10.255.255.255', 1))
		IP = testsock.getsockname()[0]
	except:
		IP = '127.0.0.1'
	finally:
		testsock.close()
	return IP

ip = get_ip()

# construct a socket
socket_type =  s.SOCK_DGRAM
sock = s.socket(s.AF_INET, socket_type)
if platform.system() not in 'Windows':
    sock.setsockopt(s.SOL_SOCKET, s.SO_REUSEADDR, 1)

def handler(signum, frame, ip=ip):
	'''
	The signal handler for the nodata alarm.

	:param int signum: signal number
	:param int frame: frame number
	:param str ip: the IP of the box this program is running on (i.e. the device the Raspberry Shake should send data to)
	:raise IOError: on UNIX systems if no data is received
	'''
	global port
	printM('ERROR: No data received in %s seconds; aborting.' % (to), sender='Init')
	printM('       Check that the Shake is forwarding data to:', sender='Init')
	printM('       IP address: %s    Port: %s' % (ip, port), sender='Init')
	printM('       and that no firewall exists between the Shake and this computer.', sender='Init')
	raise IOError('No data received')


def initRSlib(dport=port, rsstn='Z0000', timeout=10):
	'''
	.. role:: pycode(code)
		:language: python

	Initializes this library (:py:func:`rsudp.raspberryshake`).
	Set values for data port, station, network, and port timeout prior to opening the socket.
	Calls both :py:func:`rsudp.raspberryshake.openSOCK` and :py:func:`rsudp.raspberryshake.set_params`.

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')

	The library is now initialized:

	.. code-block:: python

		>>> rs.initd
		True

	:param int dport: The local port the Raspberry Shake is sending UDP data packets to. Defaults to :pycode:`8888`.
	:param str rsstn: The name of the station (something like :pycode:`'RCB43'` or :pycode:`'S0CDE'`)
	:param int timeout: The number of seconds for :py:func:`rsudp.raspberryshake.set_params` to wait for data before an error is raised (zero for unlimited wait)

	:rtype: str
	:return: The instrument channel as a string

	'''
	global port, stn, to, initd, port
	global producer
	sender = 'Init'
	printM('Initializing.', sender)
	try:						# set port value first
		if dport == int(dport):
			port = int(dport)
		else:
			port = int(dport)
			printM('WARNING: Supplied port value was converted to integer. Non-integer port numbers are invalid.')
	except ValueError as e:
		printM('ERROR: You likely supplied a non-integer as the port value. Your value: %s'
				% dport)
		printM('Error details: %s' % e)
	except Exception as e:
		printM('ERROR. Details: ' + e)

	try:						# set station name
		if len(rsstn) == 5:
			stn = str(rsstn).upper()
		else:
			stn = str(rsstn).upper()
			printM('WARNING: Station name does not follow Raspberry Shake naming convention.')
	except ValueError as e:
		printM('ERROR: Invalid station name supplied.')
		printM('Error details: %s' % e)
	except Exception as e:
		printM('ERROR. Details:' % e)
	
	try:						# set timeout value 
		to = int(timeout)
	except ValueError as e:
		printM('ERROR: You likely supplied a non-integer as the timeout value. Your value was: %s'
				% timeout)
		printM('       Continuing with default timeout of %s sec'
				% (to))
		printM('Error details: %s' % e)
	except Exception as e:
		printM('ERROR. Details: ' + e)

	initd = True				# if initialization goes correctly, set initd to true
	openSOCK()					# open a socket
	printM('Waiting for UDP data on port %s...' % (port), sender)
	set_params()				# get data and set parameters

def openSOCK(host=''):
	'''
	.. role:: pycode(code)
		:language: python

	Initialize a socket at the port specified by :pycode:`rsudp.raspberryshake.port`.
	Called by :py:func:`rsudp.raspberryshake.initRSlib`, must be done before :py:func:`rsudp.raspberryshake.set_params`.

	:param str host: self-referential location (i.e. :pycode:'localhost') at which to open a listening port
	:raise IOError: if the library is not initialized (:py:func:`rsudp.raspberryshake.initRSlib`) prior to running this function
	:raise OSError: if the program cannot bind to the specified port number

	'''
	global sockopen
	sockopen = False
	if initd:
		HP = '%s:%s' % ('localhost',port)
		printM("Opening socket on %s (HOST:PORT)"
				% HP, 'openSOCK')
		try:
			sock.bind((host, port))
			sockopen = True
		except Exception as e:
			printM('ERROR:  Could not bind to port. Is another program using it?')
			printM('Detail: %s' % e)
			raise OSError(e)
	else:
		raise IOError("Before opening a socket, you must initialize this raspberryshake library by calling initRSlib(dport=XXXXX, rssta='R0E05') first.")

def set_params():
	'''
	.. role:: pycode(code)
		:language: python

	Read a data packet off the port.
	Called by :py:func:`rsudp.raspberryshake.initRSlib`,
	must be done after :py:func:`rsudp.raspberryshake.openSOCK`
	but before :py:func:`rsudp.raspberryshake.getDATA`.
	Will wait :pycode:`rsudp.raspberryshake.to` seconds for data before raising a no data exception
	(only available with UNIX socket types).

	'''
	global to
	if os.name not in 'nt': 	# signal alarm not available on windows
		signal.signal(signal.SIGALRM, handler)
		signal.alarm(to)		# alarm time set with timeout value
	data = sock.recv(4096)
	if os.name not in 'nt':
		signal.alarm(0)			# once data has been received, turn alarm completely off
	to = 0						# otherwise it erroneously triggers after keyboardinterrupt
	getTR(getCHNS()[0])
	getSR(tf, data)
	getTTLCHN()
	printM('Available channels: %s' % chns, 'Init')
	get_inventory()

def getDATA():
	'''
	Read a data packet off the port.

	In this example, we get a Shake 1Dv7 data packet:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> d = rs.getDATA()
		>>> d
		b"{'EHZ', 1582315130.292, 14168, 14927, 16112, 17537, 18052, 17477,
		15418, 13716, 15604, 17825, 19637, 20985, 17325, 10439, 11510, 17678,
		20027, 20207, 18481, 15916, 13836, 13073, 14462, 17628, 19388}"


	:rtype: bytes
	:return: Returns a data packet as an encoded bytes object.

	:raise IOError: if no socket is open (:py:func:`rsudp.raspberryshake.openSOCK`) prior to running this function
	:raise IOError: if the library is not initialized (:py:func:`rsudp.raspberryshake.initRSlib`) prior to running this function

	'''
	global to, firstaddr
	if sockopen:
		return sock.recv(4096)
	else:
		if initd:
			raise IOError("No socket is open. Please open a socket using this library's openSOCK() function.")
		else:
			raise IOError('No socket is open. Please initialize the library using initRSlib() then open a socket using openSOCK().')
	
def getCHN(DP):
	'''
	Extract the channel information from the data packet.
	Requires :py:func:`rsudp.raspberryshake.getDATA` packet as argument.

	In this example, we get the channel code from a Shake 1Dv7 data packet:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> d = rs.getDATA()
		>>> rs.getCHN(d)
		'EHZ'

	:param DP: The Raspberry Shake UDP data packet (:py:func:`rsudp.raspberryshake.getDATA`) to parse channel information from
	:type DP: bytes
	:rtype: str
	:return: Returns the instrument channel as a string.
	'''
	return str(DP.decode('utf-8').split(",")[0][1:]).strip("\'")
	
def getTIME(DP):
	'''
	Extract the timestamp from the data packet.
	Timestamp is seconds since 1970-01-01 00:00:00Z,
	which can be passed directly to an :py:class:`obspy.core.utcdatetime.UTCDateTime` object:

	In this example, we get the timestamp of a Shake 1Dv7 data packet and convert it to a UTCDateTime:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> from obspy import UTCDateTime
		>>> d = rs.getDATA()
		>>> t = rs.getTIME(d)
		>>> t
		1582315130.292
		>>> dt = obspy.UTCDateTime(t)
		>>> dt
		UTCDateTime(2020, 2, 21, 19, 58, 50, 292000)

	:param DP: The Raspberry Shake UDP data packet (:py:func:`rsudp.raspberryshake.getDATA`) to parse time information from
	:type DP: bytes
	:rtype: float
	:return: Timestamp in decimal seconds since 1970-01-01 00:00:00Z
	'''
	return float(DP.split(b",")[1])

def getSTREAM(DP):
	'''
	Get the samples in a data packet as a list object.
	Requires :py:func:`rsudp.raspberryshake.getDATA` packet as argument.

	In this example, we get a list of samples from a Shake 1Dv7 data packet:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> d = rs.getDATA()
		>>> s = rs.getSTREAM(d)
		>>> s
		[14168, 14927, 16112, 17537, 18052, 17477, 15418, 13716, 15604,
		 17825, 19637, 20985, 17325, 10439, 11510, 17678, 20027, 20207,
		 18481, 15916, 13836, 13073, 14462, 17628, 19388]

	:param DP: The Raspberry Shake UDP data packet (:py:func:`rsudp.raspberryshake.getDATA`) to parse stream information from
	:type DP: bytes
	:rtype: list
	:return: List of data samples in the packet
	'''
	return list(map(int, DP.decode('utf-8').replace('}','').split(',')[2:]))

def getTR(chn):				# DP transmission rate in msecs
	'''
	Get the transmission rate in milliseconds between consecutive packets from the same channel.
	Must wait to receive a second packet from the same channel.
	Requires a :py:func:`rsudp.raspberryshake.getCHN` or a channel name string as argument.

	In this example, we calculate the transmission frequency of a Shake 1Dv7:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> d = rs.getDATA()
		>>> tr = rs.getTR(rs.getCHN(d))
		>>> tr
		250

	:param chn: The seismic instrument channel (:py:func:`rsudp.raspberryshake.getCHN`) to calculate transmission rate information from
	:type chn: str
	:rtype: int
	:return: Transmission rate in milliseconds between consecutive packets from a specific channel
	'''
	global tf, tr
	timeP1, timeP2 = 0.0, 0.0
	done = False
	while not done:
		DP = getDATA()
		CHAN = getCHN(DP)
		if CHAN == chn:
			if timeP1 == 0.0:
				timeP1 = getTIME(DP)
			else:
				timeP2 = getTIME(DP)
				done = True
	TR = timeP2*1000 - timeP1*1000
	tf = int(TR)
	tr = int(1000 / TR)
	return tf

def getSR(TR, DP):
	'''
	Get the sample rate in samples per second.
	Requires an integer transmission frequency and a data packet as arguments.

	In this example, we calculate the number of samples per second from a Shake 1Dv7:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> d = rs.getDATA()
		>>> tr = rs.getTR(rs.getCHN(d))
		>>> tr
		250
		>>> sps = rs.getSR(tr, d)
		>>> sps
		100


	:param TR: The transmission frequency (:py:func:`rsudp.raspberryshake.getTR`) in milliseconds between packets
	:type TR: int
	:param DP: The Raspberry Shake UDP data packet (:py:func:`rsudp.raspberryshake.getDATA`) calculate sample rate information from
	:type DP: bytes
	:rtype: int
	:return: The sample rate in samples per second from a specific channel
	'''
	global sps
	sps = int((DP.count(b",") - 1) * 1000 / TR)
	return sps
	
def getCHNS():
	'''
	Get a list of channels sent to the port.

	In this example, we list channels from a Boom:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R940D')
		>>> rs.getCHNS()
		['EHZ', 'HDF']


	:rtype: list
	:return: The list of channels being sent to the port (from the single IP address sending data)
	'''
	global chns
	chdict = {'EHZ': False, 'EHN': False, 'EHE': False,
			  'ENZ': False, 'ENN': False, 'ENE': False, 'HDF': False}
	firstCHN = ''
	done = False
	sim = 0
	while not done:
		DP = getDATA()
		if firstCHN == '':
			firstCHN = getCHN(DP)
			chns.append(firstCHN)
			continue
		nextCHN = getCHN(DP)
		if firstCHN == nextCHN:
			if sim > 1:
				done = True
				continue
			sim += 1
		else:
			chns.append(nextCHN)
	for ch in chns:
		chdict[ch] = True
	chns = []
	for ch in chdict:
		if chdict[ch] == True:
			chns.append(ch)
	return chns

def getTTLCHN():
	'''
	Calculate total number of channels received,
	by counting the number of channels returned by :py:func:`rsudp.raspberryshake.getCHNS`.

	In this example, we get the number of channels from a Shake & Boom:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R940D')
		>>> rs.getTTLCHN()
		2

	:rtype: int
	:return: The number of channels being sent to the port (from the single IP address sending data)
	'''
	global numchns
	numchns = len(getCHNS())
	return numchns


def get_inventory(sender='get_inventory'):
	'''
	.. role:: pycode(code)
		:language: python

	Downloads the station inventory from the Raspberry Shake FDSN and stores
	it as an :py:class:`obspy.core.inventory.inventory.Inventory` object which is available globally.

	In this example, we get the R940D station inventory from the Raspberry Shake FDSN:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R940D')
		>>> inv = rs.get_inventory()
		>>> print(inv)
		Inventory created at 2020-02-21T20:37:34.246777Z
			Sending institution: SeisComP3 (gempa testbed)
			Contains:
				Networks (1):
					AM
				Stations (1):
					AM.R940D (Raspberry Shake Citizen Science Station)
				Channels (2):
					AM.R940D.00.EHZ, AM.R940D.00.HDF


	:param sender: `(optional)` The name of the function calling the :py:func:`rsudp.printM` logging function
	:type str: str or None
	:rtype: obspy.core.inventory.inventory.Inventory or bool
	:return: The inventory of the Raspberry Shake station in the :pycode:`rsudp.raspberryshake.stn` variable.
	'''
	global inv, stn, region
	sender = 'get_inventory'
	if 'Z0000' in stn:
		printM('No station name given, continuing without inventory.',
				sender)
		inv = False
	else:
		try:
			printM('Fetching inventory for station %s.%s from Raspberry Shake FDSN.'
					% (net, stn), sender)
			
			inv = read_inventory('https://fdsnws.raspberryshakedata.com/fdsnws/station/1/query?network=%s&station=%s&starttime=%s&level=resp&nodata=404&format=xml'
								 % (net, stn, str(UTCDateTime.now()-timedelta(seconds=14400))))
			region = FlinnEngdahl().get_region(inv[0][0].longitude, inv[0][0].latitude)
			printM('Inventory fetch successful. Station region is %s' % (region), sender)
		except (IndexError, HTTPError):
			printM('WARNING: No inventory found for %s. Are you forwarding your Shake data?' % stn, sender)
			printM('         Deconvolution will only be available if data forwarding is on.', sender)
			printM('         Access the config page of the web front end for details.', sender)
			printM('         More info at https://manual.raspberryshake.org/quickstart.html', sender)
			inv = False
			region = False
		except Exception as e:
			printM('ERROR: Inventory fetch failed!', sender)
			printM('       Error detail: %s' % e, sender)
			inv = False
			region = False
	return inv


def make_trace(d):
	'''
	Makes a trace and assigns it some values using a data packet.

	In this example, we make a trace object with some RS 1Dv7 data:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> d = rs.getDATA()
		>>> t = rs.make_trace(d)
		>>> print(t)
		AM.R3BCF.00.EHZ | 2020-02-21T19:58:50.292000Z - 2020-02-21T19:58:50.532000Z | 100.0 Hz, 25 samples

	:param d: The Raspberry Shake UDP data packet (:py:func:`rsudp.raspberryshake.getDATA`) to parse Trace information from
	:type d: bytes
	:rtype: obspy.core.trace.Trace
	:return: A fully formed Trace object to build a Stream with
	'''
	global producer
	ch = getCHN(d)						# channel
	if ch:
		t = getTIME(d)				# unix epoch time since 1970-01-01 00:00:00Z; "timestamp" in obspy
		st = getSTREAM(d)				# samples in data packet in list [] format
		tr = Trace(data=np.ma.MaskedArray(st, dtype=np.int32))	# create empty trace
		tr.stats.network = net			# assign values
		tr.stats.location = '00'
		tr.stats.station = stn
		tr.stats.channel = ch
		tr.stats.sampling_rate = sps
		tr.stats.starttime = UTCDateTime(t)
		if inv:
			try:
				tr.attach_response(inv)
			except:
				if producer:
					printM('ERROR: Could not attach inventory response.')
					print('                           Are you sure you set the station name correctly?')
					print('                           This could indicate a mismatch in the number of data channels')
					print('                           between the inventory and the stream. For example,')
					print('                           if you are receiving RS4D data, please make sure')
					print('                           the inventory you download has 4 channels.')
				producer = False
		return tr


# Then make repeated calls to this, to continue adding trace data to the stream
def update_stream(stream, d, **kwargs):
	'''
	Returns an updated Stream object with new data, merged down to one trace per available channel.
	Most sub-consumers call this each time they receive data packets in order to keep their obspy stream current.

	In this example, we make a stream object with some RS 1Dv7 data:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> from obspy.core.stream import Stream
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> s = Stream()
		>>> d = rs.getDATA()
		>>> t = rs.make_trace(d)
		>>> s = rs.update_stream(s, d)
		>>> print(s)
		1 Trace(s) in Stream:
		AM.R3BCF.00.EHZ | 2020-02-21T19:58:50.292000Z - 2020-02-21T19:58:50.532000Z | 100.0 Hz, 25 samples


	:param obspy.core.stream.Stream stream: The stream to update
	:param d: The Raspberry Shake UDP data packet (:py:func:`rsudp.raspberryshake.getDATA`) to parse Stream information from
	:type d: bytes
	:rtype: obspy.core.stream.Stream
	:return: A seismic data stream
	'''
	while True:
		try:
			return stream.append(make_trace(d)).merge(**kwargs)
		except TypeError:
			pass


def copy(orig):
	"""
	True-copy a stream by creating a new stream and copying old attributes to it.
	This is necessary because the old stream accumulates *something* that causes
	CPU usage to increase over time as more data is added. This is a bug in obspy
	that I intend to find--or at the very least report--but until then this hack
	works fine and is plenty fast enough.

	In this example, we make a stream object with some RS 1Dv7 data and then copy it to a new stream:

	.. code-block:: python

		>>> import rsudp.raspberryshake as rs
		>>> from obspy.core.stream import Stream
		>>> rs.initRSlib(dport=8888, rsstn='R3BCF')
		>>> s = Stream()
		>>> d = rs.getDATA()
		>>> t = rs.make_trace(d)
		>>> s = rs.update_stream(s, d)
		>>> s
		1 Trace(s) in Stream:
		AM.R3BCF.00.EHZ | 2020-02-21T19:58:50.292000Z - 2020-02-21T19:58:50.532000Z | 100.0 Hz, 25 samples
		>>> s = rs.copy(s)
		>>> s
		1 Trace(s) in Stream:
		AM.R3BCF.00.EHZ | 2020-02-21T19:58:50.292000Z - 2020-02-21T19:58:50.532000Z | 100.0 Hz, 25 samples


	:param obspy.core.stream.Stream orig: The data stream to copy information from
	:rtype: obspy.core.stream.Stream
	:return: A low-memory copy of the passed data stream

	"""
	stream = Stream()
	for t in range(len(orig)):
		trace = Trace(data=orig[t].data)
		trace.stats.network = orig[t].stats.network
		trace.stats.location = orig[t].stats.location
		trace.stats.station = orig[t].stats.station
		trace.stats.channel = orig[t].stats.channel
		trace.stats.sampling_rate = orig[t].stats.sampling_rate
		trace.stats.starttime = orig[t].stats.starttime
		stream.append(trace).merge(fill_value=None)
	return stream.copy()


def deconvolve(self):
	'''
	.. role:: pycode(code)
		:language: python
	
	A central helper function for sub-consumers (i.e. :py:class:`rsudp.c_plot.Plot` or :py:class:`rsudp.c_alert.Alert`)
	that need to deconvolve their raw data to metric units.
	Consumers with :py:class:`obspy.core.stream.Stream` objects in :pycode:`self.stream` can use this to deconvolve data
	if this library's :pycode:`rsudp.raspberryshake.inv` variable
	contains a valid :py:class:`obspy.core.inventory.inventory.Inventory` object.

	:param self self: The self object of the sub-consumer class calling this function. Must contain :pycode:`self.stream` as a :py:class:`obspy.core.stream.Stream` object.
	'''
	self.stream = self.raw.copy()
	for trace in self.stream:
		trace.stats.units = self.units
		if self.deconv:
			if ('HZ' in trace.stats.channel) or ('HE' in trace.stats.channel) or ('HN' in trace.stats.channel):
				if self.deconv not in 'CHAN':
					trace.remove_response(inventory=inv, pre_filt=[0.1, 0.6, 0.95*self.sps, self.sps],
											output=self.deconv, water_level=4.5, taper=False)
				else:
					trace.remove_response(inventory=inv, pre_filt=[0.1, 0.6, 0.95*self.sps, self.sps],
											output='VEL', water_level=4.5, taper=False)
				if 'ACC' in self.deconv:
					trace.data = np.gradient(trace.data, 1)
				elif 'DISP' in self.deconv:
					trace.data = np.cumsum(trace.data)
					trace.taper(max_percentage=0.1, side='left', max_length=1)
					trace.detrend(type='demean')
				else:
					trace.stats.units = 'Velocity'
			elif ('NZ' in trace.stats.channel) or ('NE' in trace.stats.channel) or ('NN' in trace.stats.channel):
				if self.deconv not in 'CHAN':
					trace.remove_response(inventory=inv, pre_filt=[0.1, 0.6, 0.95*self.sps, self.sps],
											output=self.deconv, water_level=4.5, taper=False)
				else:
					trace.remove_response(inventory=inv, pre_filt=[0.1, 0.6, 0.95*self.sps, self.sps],
											output='ACC', water_level=4.5, taper=False)
				if 'VEL' in self.deconv:
					trace.data = np.cumsum(trace.data)
					trace.detrend(type='demean')
				elif 'DISP' in self.deconv:
					trace.data = np.cumsum(np.cumsum(trace.data))
					trace.detrend(type='linear')
				else:
					trace.stats.units = 'Acceleration'
				if ('ACC' not in self.deconv) and ('CHAN' not in self.deconv):
					trace.taper(max_percentage=0.1, side='left', max_length=1)

			else:
				trace.stats.units = 'Voltage counts'	# if this is HDF


if __name__ == '__main__':
	pass