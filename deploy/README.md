# 개발 서버 배포 (podman)

law-mcp(:7017), law_review 에이전트(:7016), 시험 페이지 playground(:7018)를 컨테이너 셋으로 띄운다.
언어 모델(gpt-oss-120b)은 기존 vLLM 서버를 그대로 부른다 — 이 구성에 포함되지 않는다.

| 구성 | 크기(대략) | 비고 |
|---|---|---|
| law-mcp 이미지 | 0.2GB | python:3.12-slim + 의존성 |
| law-review 이미지 | 0.25GB | 에이전트·playground 공용 |
| `lawdb.sqlite` | 7.4GB | 이미지에 넣지 않고 읽기 전용 볼륨으로 붙인다 |

## 1. 준비

```bash
# 저장소를 개발 서버에 받는다 (data/ 는 git 에 없다)
git clone <저장소> permit-law && cd permit-law

# 법령 DB 를 옮긴다 — 다른 경로에 두면 LAW_DATA_DIR 로 알려 준다
mkdir -p data && rsync -avP <원본 서버>:/home/ubuntu/permit/law/data/lawdb.sqlite data/
#   export LAW_DATA_DIR=/srv/permit-law/data   # 기본값은 ../data (deploy/ 기준)

# 설정 파일 — 둘 다 git 에 올리지 않는다 (.gitignore: deploy/*.env)
cd deploy
cp law-mcp.env.example law-mcp.env                 # VWORLD_API_KEY 채우기
cp ../agent/law_review/.env.example agent.env      # VLLM_BASE·VLLM_API_KEY·PUBLIC_URL 확인
```

`agent.env` 에서 볼 것:
- `VLLM_BASE`, `VLLM_MODEL`, `VLLM_API_KEY` — 개발 서버에서 vLLM 에 닿는 주소인지.
- `PUBLIC_URL` — 에이전트 카드에 실릴 바깥 주소(예: `http://dev-server:7016`). 오케스트레이터가 이 주소로 부른다.
- `MCP_SERVERS` 는 compose 가 `http://law-mcp:7017/mcp` 로 덮어쓰므로 그대로 둬도 된다.

## 2. 실행

```bash
cd deploy
podman compose up -d --build        # 또는 podman-compose up -d --build
podman compose ps
curl -s localhost:7017/health                           # {"status":"ok","docs":167006,...}
curl -s localhost:7016/.well-known/agent-card.json | head -c 200
# 브라우저: http://<개발 서버>:7018  (playground)
```

포트를 바꾸려면 `LAW_MCP_PORT`, `LAW_REVIEW_PORT`, `PLAYGROUND_PORT` 환경변수를 준다.
SELinux 서버를 위해 볼륨에 `:z` 를 붙여 두었다.

## 3. 갱신

- 코드·프롬프트: `git pull && podman compose up -d --build` — 이미지(0.5GB 안팎)만 다시 만든다.
- 설정만: `agent.env`·`law-mcp.env` 를 고치고 `podman compose restart`.
- 법령 DB: 새 `lawdb.sqlite` 를 옆에 받아 두었다가 이름을 바꿔 끼우고 `podman compose restart law-mcp`.
  (교체하는 동안 디스크가 DB 하나만큼 더 필요하다.)

## 4. 로그

```bash
podman compose logs -f law-review
podman compose logs -f law-mcp
```

도구 호출 추적(`law_mcp_trace.jsonl`)은 `logs` 볼륨에 쌓이고 playground 가 읽는다.
