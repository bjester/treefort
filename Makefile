SHELL := /bin/bash

.PHONY: build build-whl build-sdist clean codegen install

codegen:
	uv run python -m treefort.cli_codegen --output src/treefort/_cli_generated.py

build: | clean
	$(MAKE) build-whl
	$(MAKE) dist/treefort.pex
	$(MAKE) dist/treefort

dist/treefort: codegen
	uv run pyinstaller treefort.spec

dist/treefort.pex: codegen
	uv run pex -v . -e treefort.cli:main -o dist/treefort.pex

build-whl: codegen
	uv build --wheel --out-dir ./dist

build-sdist: codegen
	uv build --sdist --out-dir ./dist

clean:
	rm -rf ./build ./dist src/treefort/_cli_generated.py

install: | dist/treefort
	./dist/treefort install
