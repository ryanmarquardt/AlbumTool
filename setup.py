#!/usr/bin/env python

from distutils.core import setup
import os

def find(path='.'):
	r = []
	for f in os.listdir(path):
		if os.path.isdir(f): r.extend(find(f))
		else: r.append(os.path.join(path, f))
	return r

setup(name='albumtool', version='0.1',
	author='Ryan Marquardt',
	author_email='ryan.marquardt@gmail.com',
	description='Python program and library for archiving audio cds',
	url='http://orbnauticus.github.com/AlbumTool',
	license='Simplified BSD License',
	scripts=find('tools'),
	packages=['albumtool'],
)
