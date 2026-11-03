.PHONY: test experiments demo

PYTHON ?= python

test:
	$(PYTHON) -m pytest

experiments:
	$(PYTHON) -m experiments.run_all

demo:
	$(PYTHON) -m visualizer.app
