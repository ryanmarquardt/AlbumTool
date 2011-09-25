#!/bin/sh
export PY_FULLNAME=$(python setup.py --fullname)
export PACKAGE_NAME=$(python setup.py --name)
export PACKAGE_VERSION=$(python setup.py --version)
export PACKAGE_FULLNAME=${PACKAGE_NAME}_${PACKAGE_VERSION}
export PACKAGE_ARCH=$(dpkg-architecture -qDEB_BUILD_ARCH)
DEBUILD=$(which debuild)
SUBSHELL=${SHELL:-/bin/bash}
RM=$(which rm)
PYTHON=$(which python)
TAR=$(which tar)
DPUT=$(which dput)

verbose () { echo BUILD.SH "$@" >&2 ; "$@" ; }
indir   () { ( cd "$1" ; shift ; "$@" ) ; }
pack    () { "$RM" MANIFEST; "$PYTHON" setup.py sdist ; }
unpack  () { pack ; indir dist verbose "$TAR" -xf "${PY_FULLNAME}.tar.gz" ; }
debuild () { unpack; indir "dist/${PY_FULLNAME}" "$DEBUILD" "$@" ; }

deb () {
    ARCH=source
	case $1 in
		source)
			debuild -S -sa
			;;
		source-diff|diff)
			debuild -S -sd
			;;
		binary)
			debuild
			ARCH=${PACKAGE_ARCH}
			;;
		*)
			echo "Unknown architecture:" $1
			echo "Try one of:" source source-diff binary
			exit 2
			;;
	esac
    echo ${PACKAGE_FULLNAME}_${ARCH}.deb
}

case $1 in
	source) pack ;;
	deb) deb $2 ;;
	run)
		pack ; unpack
		export PYTHONPATH="$PWD/dist/${PY_FULLNAME}"
		if [ -n "$2" ]; then
			shift 1
			indir "dist/${PY_FULLNAME}" "$@"
		else
			echo "Starting subshell with proper environment..."
			indir "dist/${PY_FULLNAME}" "$SUBSHELL"
		fi
		;;
	ppa-upload|ppa)
		deb source-diff
		if [ -n "$2" ]; then
			PPA="$2"
		elif [ -z "$PPA" ] ; then
			read -p "Which ppa would you like to upload to?" PPA
		fi
		"$DPUT" "$PPA" "dist/${PACKAGE_FULLNAME}_source.changes"
		;;
	*)
		echo "Unknown Command:" $1
		echo "Try one of:" source deb run ppa
		exit 1
		;;
esac
