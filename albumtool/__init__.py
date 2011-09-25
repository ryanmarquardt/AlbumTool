#!/usr/bin/env python

try:
	import CDDB, DiscID
except ImportError, e:
	print e
	print 'Try installing python-cddb'
	exit(128)
from itertools import tee, izip, imap
import optparse
import os
from subprocess import Popen,PIPE
import sys
import StringIO
import tarfile
import time
import wave

SECTORS_PER_SECOND = 75
SECTORS_PER_MINUTE = SECTORS_PER_SECOND * 60

def pairwise(i):
	a,b = tee(i)
	next(b,None)
	return izip(a,b)

def run(*cmd, **kwargs):
	return Popen(cmd,
		stdin=kwargs.get('stdin',None),
		stdout=PIPE,
		stderr=kwargs.get('stderr',None),
	)

def addStrToTar(tar, name, s):
	info = tarfile.TarInfo(name)
	info.mtime = long(time.time())
	info.size = len(s)
	tar.addfile(info, StringIO.StringIO(s))

wdevnull = lambda:open(os.devnull,'w')
taropen = lambda t,f:run('tar', '-xf', t, f, '-O', stderr=wdevnull())

class Timestamp(int):
	@classmethod
	def read(cls, string):
		m,s,f = map(int,string.replace(':','.').split('.'))
		return cls(m*SECTORS_PER_MINUTE + s*SECTORS_PER_SECOND + f)
	def __str__(self):
		return '%02d:%02d.%02d' % (self/4500,self/75%60,self%75)
	__repr__ = __str__

class GstPipeProc(object):
	def __init__(self, pipe_rep):
		self.pipe_rep = pipe_rep

	def run(self, metadata):
		cmd = ['gst-launch-0.10', '-t',
		'fdsrc', '!',
		'taginject', 'tags=artist=\"{artist}\",album=\"{album}\",date=\"{date}\",track-number=\"{track}\",title=\"{title}\",genre=\"{genre}\"', '!',
		'decodebin', '!',
		'audioconvert', '!',
		'{0}'.format( self.pipe_rep, **metadata ), '!',
		'filesink location="{file}"'.format(self.pipe_rep, **metadata)]
		print cmd
		self.proc = Popen(cmd, stdin=PIPE, preexec_fn=lambda:os.nice(10))
		return self.proc, self.proc.stdin

GstPipe = GstPipeProc

class WaveSplitter(object):
	def __init__(self, src=sys.stdin):
		self.src = wave.open(src)
		self.params = self.src.getparams()
		self.progress = 0

	nchannels = property(lambda x:x.params[0])
	sampwidth = property(lambda x:x.params[1])
	framerate = property(lambda x:x.params[2])
	nframes = property(lambda x:x.params[3])

	def write_sectors(self, path, sectors):
		frames = sectors * self.framerate / SECTORS_PER_SECOND
		s = StringIO.StringIO()
		if hasattr(path, 'write'):
			dest = path
		else:
			dest = open(path, 'wb')
		buff = wave.open(s,'wb')
		buff.setparams(self.params)
		buff.setnframes(frames)
		buff.writeframesraw(self._read(frames))
		dest.write(s.getvalue())
		dest.close()

	def _read(self, frames):
		self.progress += frames
		return self.src.readframes(frames)

class CueFile(object):
	def __init__(self, fobj):
		self.File = None
		self.Tracks = [{'remarks':[]}]
		for line in fobj:
			directive,args = line.split(None,1)
			args = args.strip()
			if directive == 'FILE':				self.File = args[1:-6]
			elif directive == 'INDEX':			self.Tracks[-1]['index'] = args.split()[1]
			elif directive == 'PERFORMER':		self.Tracks[-1]['performer'] = args.strip('"')
			elif directive == 'TITLE':			self.Tracks[-1]['title'] = args.strip('"')
			elif directive == 'REM':
				word,rest = args.split(None,1)
				if word == 'GENRE':				self.Tracks[-1]['genre'] = rest
				elif word == 'DATE':			self.Tracks[-1]['date'] = rest
				else:							self.Tracks[-1]['remarks'].append(line[3:])
			elif directive == 'TRACK':
				idx,typ = args.split()
				if int(idx) != len(self.Tracks) or typ != 'AUDIO':
					raise IOError('Malformed cue file. Expected "TRACK %02d AUDIO"' % len(self.Tracks))
				self.Tracks.append({'remarks':[]})
			else:
				print >>sys.stderr, 'Unknown directive:', `directive`, `args`
		for t in self.Tracks:
			if 'index' in t:
				t['firstsector'] = Timestamp.read(t['index'])
		for s,t in pairwise(self.Tracks[1:]):
			s['nsectors'] = t['firstsector'] - s['firstsector']
	def __iter__(self):
		return iter(self.Tracks[1:])
	def __str__(self):
		return str(self.__dict__)
	@property
	def ntracks(self):
		return len(self.Tracks[1:])

class Album(object):
	def __init__(self, path):
		self.path = path
		cuetar = taropen(self.path, 'cue')
		self.Cue = CueFile(cuetar.stdout)
		AudioSrc = taropen(self.path, self.Cue.File)
		if self.Cue.File.endswith('.flac'):
			self.AudioFile = Popen(['flac','-d','-s','-'],
			  stdin=AudioSrc.stdout, stdout=PIPE).stdout
		elif self.Cue.File.endswith('.wv'):
			self.AudioFile = Popen(['wvunpack','-','-o','-','-q'],
			  stdin=AudioSrc.stdout, stdout=PIPE).stdout
		elif not self.Cue.File.endswith('.wav'):
			self.AudioFile = Popen('gst-launch-0.10 -q fdsrc ! decodebin ! audio/x-raw-int ! wavenc ! fdsink',
			  shell=True, stdin=AudioSrc.stdout, stdout=PIPE, bufsize=-1).stdout
		else:
			self.AudioFile = AudioSrc.stdout

def cached(func):
	def f(path, *args, **kwargs):
		print 'Reading cache:', path
		try:
			return eval(open(path,'r').read())
		except BaseException, e:
			print >>sys.stderr, 'No cached value'
			print e
			r = func(*args, **kwargs)
			print >>sys.stderr, r
			try:
				dir = os.path.dirname(path)
				if not os.path.exists(dir): os.makedirs(dir)
				open(path,'w').write(repr(r))
			except BaseException, e:
				print e
			finally:
				return r
	f.func_name = func.func_name
	return f

def cddb_query(discid, cache_dir=os.path.expanduser('~/.cache/cddb')):
	cache_path = os.path.join(cache_dir, '%08x' % discid[0])
	query = cached(lambda:CDDB.query(discid)[1])(cache_path, discid)
	categories = {}
	if query is None:
		raise Exception('Server has no data for this disc')
	if isinstance(query, dict):
		query = [query]
	if isinstance(query, list):
		for q in query:
			categories[q['category']] = q['title']
		return categories

class CD(object):
	def __init__(self, dev=None, discid=None, category=None, cache=os.path.expanduser('~/.cache/cddb')):
		self.dev = dev or '/dev/cdrom'
		self.cache_dir = cache
		self.discid = discid or DiscID.disc_id(open(self.dev,'rb'))
		if category:
			self.set_category(category)
		else:
			self.categories = cddb_query(self.discid)
			self._clear_md()

	def _clear_md(self):
		self.metadata = [{'discid':self.fingerprint}]
		tracks = list(self.tracks)
		tracks.append(self.duration)
		for i in range(self.track_count):
			self.metadata.append({
				'start':Timestamp(tracks[i]),
				'duration':Timestamp(tracks[i+1]-tracks[i]),
			})

	fingerprint = property(lambda x:'%08x' % x.discid[0])
	track_count = property(lambda x:x.discid[1])
	duration = property(lambda x:Timestamp(int(x.discid[-1])*75))
	tracks = property(lambda x:imap(lambda y:Timestamp(y-150),x.discid[2:-1]))

	def set_category(self, cat):
		self._clear_md()
		cache_path = os.path.join(self.cache_dir, '{0}-{1}'.format(self.fingerprint, cat))
		for k,v in cached(lambda:CDDB.read(cat, self.fingerprint)[1])(cache_path).items():
			if not v or k[0] not in 'DTE':
				continue
			elif k == 'DTITLE':
				self.metadata[0]['artist'],_,self.metadata[0]['title'] = v.partition(' / ')
			elif k == 'DGENRE': self.metadata[0]['genre'] = v
			elif k == 'DYEAR':  self.metadata[0]['year'] = v
			elif k.startswith('TTITLE'): self.metadata[int(k[6:])+1]['title'] = v

	def save(self, path, format='flac'):
		path = path.format(**self.metadata[0])
		album = tarfile.open(path, 'w:')
		cdp = run('cdparanoia', '-e', '-d', self.dev, '1-', '-', stderr=wdevnull())
		if format == 'wav':
			audiopath = path.rpartition('.')[0] + '.wav'
			out = cdp
		elif format == 'flac':
			audiopath = path.rpartition('.')[0] + '.flac'
			flc = run('gst-launch-0.10', '-q', 'fdsrc', '!', 'decodebin', '!', 'audioconvert', '!', 'flacenc', '!', 'fdsink', stdin=cdp.stdout)
			out = flc
		data = out.communicate()[0]
		addStrToTar(album, 'cue', self.get_cuesheet(audiopath))
		addStrToTar(album, audiopath, data)

	def get_cuesheet(self, audiopath):
		es = lambda s:unicode('"%s"' % s.replace('\\', r'\\').replace('"', r'\"'), 'iso-8859-1')
		s = StringIO.StringIO()
		if 'title' in self.metadata[0]:  print >>s, 'TITLE', es(self.metadata[0]['title'])
		if 'artist' in self.metadata[0]: print >>s, 'PERFORMER', es(self.metadata[0]['artist'])
		print >>s, 'REM DISCID', self.fingerprint
		if 'genre' in self.metadata[0]:  print >>s, 'REM GENRE', es(self.metadata[0]['genre'])
		print >>s, 'FILE "{0}" WAVE'.format(audiopath)
		for i,ts in enumerate(self.metadata):
			if i:
				print >>s, '  TRACK %02d AUDIO' % i
				if 'title' in ts:  print >>s, '    TITLE', es(ts['title'])
				if 'artist' in ts: print >>s, '    PERFORMER', es(ts['artist'])
				print >>s, '    INDEX 01', ts['start']
		return s.getvalue()

