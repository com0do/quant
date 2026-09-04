# Project-root Makefile: currently only manages the Jupyter kernel (running .ipynb files in VS Code / Cursor).
#
#   make jupyter     install the jupyter dependency group + register the kernel + fix test/*.ipynb + status check
#   make status      read-only status (pure shell, imports no packages)
#   make clean-venv  delete .venv and unregister the kernel together
#   make lab         fallback: browser-based JupyterLab
#
# The kernel and .venv live and die together:
#   - the kernel name comes from [project].name in pyproject.toml and is never hard-coded;
#   - the kernel.json argv is pinned to this project's .venv/bin/python; a same-name kernel without that path is treated as another clone and is never overwritten or deleted;
#   - clean-venv unregisters the kernel before deleting .venv;
#   - the .ipynb metadata holds the same kernel name, so after rebuilding .venv or moving machines there is no need to pick a kernel by hand.
#
# Why the "select kernel" prompt kept appearing: the user-level python3 kernel has a bare "python" argv (any interpreter on PATH),
# while the notebook metadata said name=python3 / display_name=.venv; the two share a name, so VS Code could not tell which one to use.

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJECT := $(shell awk -F'"' '/^name[[:space:]]*=/ {print $$2; exit}' $(ROOT)/pyproject.toml)
ifeq ($(strip $(PROJECT)),)
$(error cannot read [project].name from pyproject.toml)
endif

UV           ?= uv
VENV         := $(ROOT)/.venv
PY           := $(VENV)/bin/python
KERNEL_NAME  := $(PROJECT)
KERNEL_DISP  := $(PROJECT) (.venv)
JUPYTER_DATA := $(if $(JUPYTER_DATA_DIR),$(JUPYTER_DATA_DIR),$(if $(XDG_DATA_HOME),$(XDG_DATA_HOME),$(HOME)/.local/share)/jupyter)
KERNEL_DIR   := $(JUPYTER_DATA)/kernels/$(KERNEL_NAME)
NB           ?= $(wildcard $(ROOT)/test/*.ipynb)
PORT         ?= 8888

# Whether kernel.json belongs to this project's .venv: its argv contains the quoted $(PY).
# It must be wrapped in a subshell, otherwise the ! in `! $(OWNED)` only negates the first part of the && chain.
OWNED = ( [ -f "$(KERNEL_DIR)/kernel.json" ] && grep -qF '"$(PY)"' "$(KERNEL_DIR)/kernel.json" )

.PHONY: help jupyter venv kernel kernel-rm nb-kernel status clean-venv lab

help: ## Show this help
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z0-9_-]+:.*## /{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

jupyter: kernel nb-kernel status ## One-shot: install jupyter deps + register the kernel + fix notebooks + status

venv: ## Sync .venv (runtime deps only; does not touch uv.lock and does not uninstall hand-installed extras)
	cd $(ROOT) && $(UV) sync --frozen --inexact

kernel: ## Install the jupyter dependency group and register .venv as a kernel (FORCE=1 overwrites a same-name kernel pointing elsewhere)
	cd $(ROOT) && $(UV) sync --frozen --inexact --group jupyter
	@if [ -f "$(KERNEL_DIR)/kernel.json" ] && ! $(OWNED) && [ -z "$(FORCE)" ]; then \
		echo "kernel $(KERNEL_NAME) already exists but does not belong to this project's .venv: $(KERNEL_DIR)/kernel.json"; \
		echo "To overwrite it, run: make kernel FORCE=1"; exit 1; fi
	$(PY) -m ipykernel install --user --name $(KERNEL_NAME) --display-name "$(KERNEL_DISP)"

kernel-rm: ## Unregister the kernel (only if it belongs to this project's .venv)
	@if $(OWNED); then rm -rf "$(KERNEL_DIR)" && echo "unregistered kernel $(KERNEL_NAME)"; \
	else echo "no kernel $(KERNEL_NAME) belongs to this project's .venv, nothing to unregister"; fi

nb-kernel: venv ## Point the kernelspec of NB (default test/*.ipynb) at this project's kernel; metadata only
	$(PY) $(ROOT)/scripts/jupyter_nb_kernel.py --name $(KERNEL_NAME) --display "$(KERNEL_DISP)" $(NB)

status: ## Read-only status: project name / .venv / kernel / notebook kernel names
	@echo "project  : $(PROJECT)"
	@if [ -x "$(PY)" ]; then echo ".venv    : OK   $(PY)"; else echo ".venv    : missing (make venv)"; fi
	@if $(OWNED); then \
		if [ -x "$(PY)" ]; then echo "kernel   : OK   $(KERNEL_NAME) -> $(PY)"; \
		else echo "kernel   : dangling $(KERNEL_NAME) points to a .venv that no longer exists (make kernel-rm)"; fi; \
	elif [ -f "$(KERNEL_DIR)/kernel.json" ]; then echo "kernel   : mismatch $(KERNEL_NAME) belongs to another interpreter (make kernel FORCE=1 to overwrite)"; \
	else echo "kernel   : not registered (make kernel)"; fi
	@for f in $(NB); do \
		if grep -q '"name": "$(KERNEL_NAME)"' "$$f"; then echo "notebook : OK   $${f#$(ROOT)/}"; \
		else echo "notebook : needs fixing $${f#$(ROOT)/} (make nb-kernel)"; fi; done

clean-venv: kernel-rm ## Delete .venv and unregister the kernel bound to it (they live and die together)
	rm -rf $(VENV)

lab: kernel ## Fallback: browser-based JupyterLab (uv run --with, temporary dependency; pyproject and uv.lock untouched)
	cd $(ROOT) && $(UV) run --frozen --with jupyterlab jupyter lab \
		--no-browser --ip=127.0.0.1 --port=$(PORT) --notebook-dir=$(ROOT)
