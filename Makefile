.PHONY: test experiments demo real-results demo-real

PYTHON ?= python

test:
	$(PYTHON) -m pytest

experiments:
	$(PYTHON) -m experiments.run_all

demo:
	$(PYTHON) -m visualizer.app

real-results:
	$(PYTHON) -m experiments.real_data --scenarios data/processed --out results/real

demo-real:
	$(PYTHON) -m visualizer.app --scenarios data/processed --results results/real
