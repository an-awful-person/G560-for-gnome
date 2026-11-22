.PHONY: install uninstall test probe status udev

install:
	./install.sh install

uninstall:
	./install.sh uninstall

test:
	python3 -m unittest discover -s tests -t . -v
	gjs -m tests/test_colors.js

probe:
	python3 -m g560d probe

status:
	python3 -m g560d status

# Only needed if the speaker's hidraw node is not user-accessible.
udev:
	./install.sh install --udev
