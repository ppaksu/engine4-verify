all:
	uv run agent.py

check: build verify render

build:
	uv run --group demo engine/make_engine.py

verify:
	uv run verify.py

render:
	uv run render.py

test:
	uv run --group demo python tests/check_pipeline.py

# 다시 만들 수 있는 결과물만 삭제
clean:
	rm -rf parts reports

# 처음 상태로: 엔진 코드, 프롬프트 기록, 시도 코드까지 삭제 (확인 후)
reset:
	@printf "엔진 코드, prompts/log.csv, prompts/attempts 까지 지웁니다. 계속? [y/N] "; read a; [ "$$a" = y ]
	rm -rf parts reports prompts/attempts engine/make_engine.py
	printf '날짜,부품,도구,프롬프트,첨부,결과,메모\n' > prompts/log.csv

.PHONY: all check build verify render test clean reset
