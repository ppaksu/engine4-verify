all: build verify render

build:
	uv run --group demo engine/make_engine.py

verify:
	uv run verify.py

render:
	uv run render.py

test:
	uv run --group demo python tests/check_pipeline.py

.PHONY: all build verify render test
