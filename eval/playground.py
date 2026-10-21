#!/usr/bin/env python3
"""law_review 에이전트를 브라우저에서 시험하는 페이지.

  python3 eval/playground.py [--port 7018] [--agent http://localhost:7016] [--trace logs/law_mcp_trace.jsonl]

질문을 A2A(message:send)로 보내고 태스크가 끝날 때까지 기다린 뒤, 답변과 그 사이 law-mcp 가 받은
도구 호출(LAW_MCP_TRACE_FILE)을 함께 보여 준다. 되묻기(INPUT_REQUIRED)에 답하면 같은 태스크로 이어 보낸다.
표준 라이브러리만 쓴다. 혼자 쓰는 시험용이라 동시 요청의 추적 행이 섞일 수 있다.
"""
import argparse
import json
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_INPUT_REQUIRED",
            "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}
ARGS = None


def _req(url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                               headers={"Content-Type": "application/json", "A2A-Version": "1.0"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def _memory(task):
    for a in task.get("artifacts") or []:
        if a.get("name") == "memory":
            return a["parts"][0].get("data")
    return None


def _answer(task):
    for a in task.get("artifacts") or []:
        if a.get("name") == "answer":
            return a["parts"][0].get("text", "")
    msg = (task.get("status") or {}).get("message") or {}
    return " ".join(p.get("text", "") for p in msg.get("parts", []))


def ask(text, task_id=None, context_id=None, memory=None):
    """오케스트레이터 흉내: 같은 대화는 같은 contextId 로, 에이전트가 낸 memory 는 다음 호출의 data 파트로 돌려준다."""
    trace = Path(ARGS.trace)
    before = trace.read_text().count("\n") if trace.exists() else 0
    t0 = time.time()
    parts = [{"text": text}]
    if memory and not task_id:          # 재개 턴은 태스크가 맥락을 잇는다
        parts.append({"data": {"memory": memory}})
    msg = {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": parts}
    if task_id:
        msg["taskId"] = task_id
    if context_id:
        msg["contextId"] = context_id
    task = _req(ARGS.agent + "/message:send", {"message": msg, "configuration": {"returnImmediately": True}})["task"]
    end = time.time() + 300
    while time.time() < end:
        task = _req(f"{ARGS.agent}/tasks/{task['id']}")
        if task["status"]["state"] in TERMINAL:
            break
        time.sleep(1.0)
    rows = []
    if trace.exists():
        for line in trace.read_text().splitlines()[before:]:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            rows.append({"tool": d.get("tool"), "args": d.get("args"), "status": d.get("status"),
                         "근거상태": d.get("근거상태"), "message": d.get("message"),
                         "refs": [f"{l} {a}" for l, a in (d.get("refs") or [])][:20]})
    return {"state": task["status"]["state"].replace("TASK_STATE_", ""), "answer": _answer(task),
            "taskId": task["id"], "contextId": task.get("contextId"), "tools": rows, "memory": _memory(task),
            "seconds": round(time.time() - t0, 1)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/ask":
            return self._send(404, "not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            out = ask(body["text"], body.get("taskId"), body.get("contextId"), body.get("memory"))
            self._send(200, json.dumps(out, ensure_ascii=False), "application/json; charset=utf-8")
        except Exception as e:  # noqa: BLE001 — 시험 페이지: 오류를 그대로 보여 준다
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        pass


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>법령 근거 시험대</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--sub:#6b6b70;--line:#e3e3e0;--accent:#2457c5;--ok:#1f7a3a;--warn:#a15c00;--bad:#b42318;--code:#f1f1ee}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151517;--card:#1f1f22;--ink:#ececee;--sub:#a0a0a8;--line:#333338;--accent:#7aa2ff;--ok:#5fcf80;--warn:#f0b35a;--bad:#ff7a6e;--code:#2a2a2e}}
:root[data-theme="dark"]{--bg:#151517;--card:#1f1f22;--ink:#ececee;--sub:#a0a0a8;--line:#333338;--accent:#7aa2ff;--ok:#5fcf80;--warn:#f0b35a;--bad:#ff7a6e;--code:#2a2a2e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px 80px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--sub);font-size:13px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px}
textarea{width:100%;min-height:70px;resize:vertical;border:1px solid var(--line);border-radius:8px;padding:10px;background:var(--bg);color:var(--ink);font:inherit}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:7px 12px;font:inherit;font-size:13px;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-size:14px;padding:8px 18px}button:disabled{opacity:.5;cursor:wait}
.ex{font-size:12.5px}.badge{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;border:1px solid currentColor}
.COMPLETED{color:var(--ok)}.INPUT_REQUIRED{color:var(--warn)}.FAILED,.REJECTED,.ERROR{color:var(--bad)}
.q{font-weight:600;margin-bottom:8px}.meta{color:var(--sub);font-size:12.5px;margin-left:6px}
.answer table{border-collapse:collapse;width:100%;font-size:13.5px;display:block;overflow-x:auto}.answer th,.answer td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
.answer code,pre{background:var(--code);border-radius:4px}pre{padding:8px;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-all}
details{margin-top:10px;font-size:13px}summary{cursor:pointer;color:var(--sub)}.tool{border-top:1px dashed var(--line);padding:6px 0}
.tool b{font-family:ui-monospace,monospace}.hint{color:var(--warn);font-size:13px;margin-top:8px}
</style></head><body><main>
<h1>법령 근거 시험대</h1>
<div class="sub">law_review 에이전트(A2A) → law-mcp. 답변과 함께 에이전트가 부른 도구·근거상태·참조 조문을 보여 줍니다.
이 페이지가 오케스트레이터 역할을 해서, 같은 대화 안에서는 에이전트가 낸 작업 기억을 다음 질문에 돌려줍니다. 끊으려면 "새 대화".</div>
<div class="card">
<textarea id="q" placeholder="예: 경상북도 영천시 금호읍 신월리 1-51에 공장 지을 수 있어?"></textarea>
<div class="row"><button class="primary" id="send">보내기</button><button id="reset">새 대화</button><span id="cont" class="meta"></span></div>
<div class="row" id="examples"></div>
</div>
<div id="log"></div>
</main>
<script>
const EX=["경상북도 영천시 금호읍 신월리 1-51에 공장 지을 수 있어?","영천시 자연녹지지역에 일반음식점 낼 수 있어?","천안시 자연녹지지역에 공장 지을 수 있어?",
"부산 해운대구 제1종일반주거지역에 카페 열 수 있어?","청주시 자연녹지지역 임야에 주유소 지으려면 근거 법령이랑 필요한 허가가 뭐야?",
"국토계획법 시행령 제84조 건폐율 규정이 영천시 조례에서는 어떻게 정해져 있어?","건축법 제11조 건축허가에서 대통령령으로 정한 내용이 뭐야? 하위 법령 조문까지 찾아줘",
"영천시에서 가축 사육 제한 거리는 어느 조례에 있어?","중구 자연녹지지역에 창고 지어도 돼?"];
const $=s=>document.querySelector(s);let pending=null,conv={contextId:null,memory:null};
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const md=s=>window.marked?marked.parse(s||""):"<pre>"+esc(s)+"</pre>";
EX.forEach(t=>{const b=document.createElement("button");b.className="ex";b.textContent=t.length>34?t.slice(0,34)+"…":t;b.title=t;b.onclick=()=>{$("#q").value=t;$("#q").focus()};$("#examples").appendChild(b)});
function setCont(){const n=conv.memory?.turns?.length||0;$("#cont").textContent=pending?"되묻기에 답하는 중 — 같은 태스크로 이어 보냅니다":(n?`같은 대화 · 작업 기억 ${n}턴을 함께 보냅니다`:"")}
$("#reset").onclick=()=>{pending=null;conv={contextId:null,memory:null};setCont();$("#log").innerHTML=""};
async function send(){const text=$("#q").value.trim();if(!text)return;$("#send").disabled=true;
 const card=document.createElement("div");card.className="card";card.innerHTML=`<div class="q">${esc(text)}</div><div class="meta">에이전트가 답을 찾는 중…</div>`;$("#log").prepend(card);
 try{const r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text,contextId:conv.contextId,memory:conv.memory,...(pending||{})})});
  const d=await r.json();if(d.error)throw new Error(d.error);
  pending=d.state==="INPUT_REQUIRED"?{taskId:d.taskId,contextId:d.contextId}:null;
  conv.contextId=d.contextId||conv.contextId;if(d.memory)conv.memory=d.memory;setCont();
  const tools=d.tools.map(t=>`<div class="tool"><b>${esc(t.tool)}</b> <span class="meta">${esc(t.status)}${t["근거상태"]?" · "+esc(t["근거상태"]):""}</span><pre>${esc(JSON.stringify(t.args,null,0))}</pre>${t.message?`<div class="meta">${esc(t.message)}</div>`:""}${t.refs.length?`<div class="meta">refs: ${esc(t.refs.join(" · "))}</div>`:""}</div>`).join("");
  card.innerHTML=`<div class="q">${esc(text)}</div><div><span class="badge ${d.state}">${d.state}</span><span class="meta">${d.seconds}초 · 도구 ${d.tools.length}회</span></div>
   <div class="answer">${md(d.answer)}</div>${d.state==="INPUT_REQUIRED"?`<div class="hint">에이전트가 되묻고 있습니다. 위 입력창에 답하면 이어서 진행합니다.</div>`:""}
   <details><summary>도구 호출 ${d.tools.length}회</summary>${tools||'<div class="meta">없음</div>'}</details>`;
  $("#q").value="";
 }catch(e){card.innerHTML=`<div class="q">${esc(text)}</div><span class="badge ERROR">오류</span><pre>${esc(e.message)}</pre>`}
 finally{$("#send").disabled=false}}
$("#send").onclick=send;$("#q").addEventListener("keydown",e=>{if(e.key==="Enter"&&(e.ctrlKey||e.metaKey))send()});
</script></body></html>"""


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7018)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--agent", default="http://localhost:7016")
    ap.add_argument("--trace", default=str(ROOT / "logs" / "law_mcp_trace.jsonl"))
    ARGS = ap.parse_args()
    print(f"playground: http://{ARGS.host}:{ARGS.port}  (agent {ARGS.agent})", flush=True)
    ThreadingHTTPServer((ARGS.host, ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
