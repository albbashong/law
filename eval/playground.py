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
                         "refs": [f"{l} {a}" for l, a in (d.get("refs") or [])][:20],
                         "ref_pairs": [[l, a] for l, a in (d.get("refs") or [])][:40],
                         "ref_texts": d.get("ref_texts") or []})
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
<title>법령 근거 찾기</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600&family=Noto+Serif+KR:wght@400;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<style>
:root{--paper:#f4f5f7;--leaf:#fff;--ink:#1c2333;--muted:#5b6475;--rule:#dde1e7;--seal:#b8322a;--seal-soft:#f8e9e7;--caution:#8a5a00;--caution-soft:#fff4dc;--ok:#256b45;--focus:#2f5bd3;
 --ui:"IBM Plex Sans KR",system-ui,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;--text:"Noto Serif KR","AppleMyungjo",serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#14171d;--leaf:#1b1f27;--ink:#e6e8ee;--muted:#9aa2b2;--rule:#2d333e;--seal:#e0685e;--seal-soft:#3a2321;--caution:#e8b45a;--caution-soft:#3a2f17;--ok:#6fcf97;--focus:#7aa2ff}}
:root[data-theme="dark"]{--paper:#14171d;--leaf:#1b1f27;--ink:#e6e8ee;--muted:#9aa2b2;--rule:#2d333e;--seal:#e0685e;--seal-soft:#3a2321;--caution:#e8b45a;--caution-soft:#3a2f17;--ok:#6fcf97;--focus:#7aa2ff}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 var(--ui);display:flex;flex-direction:column;word-break:keep-all;overflow-wrap:anywhere}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
button{font:inherit;color:inherit;cursor:pointer}
header{display:flex;align-items:baseline;gap:16px;padding:14px 24px;border-bottom:1px solid var(--rule);background:var(--leaf)}
header h1{font:600 17px/1.3 var(--ui);margin:0;letter-spacing:-.01em}
header p{margin:0;color:var(--muted);font-size:13px;flex:1}
.ghost{border:1px solid var(--rule);background:transparent;border-radius:6px;padding:5px 12px;font-size:13px}
.ghost:hover{border-color:var(--muted)}
.shell{flex:1;display:grid;grid-template-columns:minmax(0,1fr) 300px;min-height:0}
#thread{overflow-y:auto;padding:28px 24px 24px}
.col{max-width:760px;margin:0 auto}

/* 빈 화면 */
.welcome h2{font:600 26px/1.35 var(--text);margin:8px 0 10px;letter-spacing:-.02em}
.welcome>p{color:var(--muted);margin:0 0 28px;max-width:38em}
.group{margin-bottom:22px}
.group h3{font:600 13px/1.4 var(--ui);color:var(--muted);margin:0 0 8px}
.group button{display:block;width:100%;text-align:left;background:var(--leaf);border:1px solid var(--rule);border-radius:8px;padding:10px 14px;margin-bottom:6px;font-size:14.5px}
.group button:hover{border-color:var(--seal)}

/* 대화 */
.turn{padding-bottom:40px}
.sep{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:12.5px;font-weight:600;margin:0 0 18px}
.sep::before,.sep::after{content:"";flex:1;height:1px;background:var(--rule)}
.q{width:fit-content;max-width:85%;margin:0 0 16px auto;background:var(--ink);color:var(--leaf);padding:11px 16px;border-radius:14px 14px 4px 14px;font:500 15.5px/1.55 var(--ui);white-space:pre-wrap}
.meta{color:var(--muted);font-size:12.5px;margin:0 0 10px}
.meta .state{font-weight:600}
.meta .COMPLETED{color:var(--ok)}.meta .INPUT_REQUIRED{color:var(--caution)}.meta .FAILED,.meta .REJECTED,.meta .ERROR{color:var(--seal)}
.answer{background:var(--leaf);border:1px solid var(--rule);border-radius:10px;padding:18px 22px;font:15.5px/1.85 var(--text)}
.answer h1,.answer h2,.answer h3,.answer h4{font:600 16px/1.5 var(--ui);margin:18px 0 6px}
.answer p{margin:0 0 10px}.answer ul,.answer ol{padding-left:22px;margin:0 0 10px}
.answer strong{font-weight:600}
.answer table{border-collapse:collapse;width:100%;font:13.5px/1.6 var(--ui);margin:10px 0 14px;display:block;overflow-x:auto}
.answer th,.answer td{border-bottom:1px solid var(--rule);padding:7px 10px;text-align:left;vertical-align:top}
.answer th{font-weight:600;color:var(--muted);border-bottom:1.5px solid var(--ink)}
.answer code{background:var(--paper);border-radius:4px;padding:0 4px;font-size:.9em}
.answer hr{border:0;border-top:1px solid var(--rule);margin:16px 0}
.answer .unverified{font:13.5px/1.6 var(--ui);background:var(--caution-soft);color:var(--caution);border-radius:6px;padding:9px 12px;margin-top:14px}
.asking{font:14px/1.6 var(--ui);color:var(--caution);margin:10px 0 0}
.waiting{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:14px;padding:6px 0}
.waiting i{width:9px;height:9px;border-radius:50%;background:var(--seal);animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.waiting i{animation:none}}
details.trail{margin:12px 0 0;font-size:13px}
details.trail summary{cursor:pointer;color:var(--muted)}
.step{display:grid;grid-template-columns:20px 1fr;gap:4px 8px;padding:8px 0;border-bottom:1px dashed var(--rule)}
.step .n{color:var(--muted);font-variant-numeric:tabular-nums}
.step b{font-weight:600}.step .args{color:var(--muted)}
.step .msg{grid-column:2;color:var(--muted);font-size:12.5px}
.step.failed b{color:var(--seal)}
.error{color:var(--seal);font-size:14px}

/* 근거 색인 */
#index{border-left:1px solid var(--rule);background:var(--leaf);overflow-y:auto;padding:22px 0 22px 18px}
#index h2{font:600 14px/1.4 var(--ui);margin:0 18px 4px 0}
#index>p{color:var(--muted);font-size:12.5px;margin:0 18px 16px 0}
.law{margin-bottom:14px}
.law h3{font:600 13.5px/1.45 var(--text);margin:0 18px 5px 0}
.tab{display:block;width:100%;text-align:left;background:var(--paper);border:0;border-right:4px solid var(--seal);border-radius:4px 0 0 4px;padding:4px 10px;margin-bottom:3px;font:13px/1.5 var(--ui)}
.tab:hover{background:var(--seal-soft)}
details.art{margin-bottom:3px}
details.art>summary.tab{list-style:none;cursor:pointer;margin:0}
details.art>summary.tab::-webkit-details-marker{display:none}
details.art>summary.tab::after{content:" ▸";color:var(--muted)}
details.art[open]>summary.tab::after{content:" ▾"}
.src{margin:4px 18px 6px 0;padding:8px 10px;border-left:2px solid var(--rule);font:12.5px/1.65 var(--text);white-space:pre-wrap;max-height:320px;overflow-y:auto}
.ask{background:none;border:0;color:var(--focus);font:12px/1.4 var(--ui);padding:0 0 6px;cursor:pointer;text-decoration:underline}

/* 입력 */
.composer{border-top:1px solid var(--rule);background:var(--leaf);padding:12px 24px 16px}
.composer .col{display:flex;gap:10px;align-items:flex-end}
textarea{flex:1;min-height:48px;max-height:180px;resize:none;border:1px solid var(--rule);border-radius:10px;padding:12px 14px;background:var(--paper);color:var(--ink);font:15px/1.5 var(--ui)}
textarea:focus{outline:none;border-color:var(--focus)}
.send{background:var(--ink);color:var(--leaf);border:0;border-radius:10px;padding:12px 20px;font-weight:600}
.send:disabled{opacity:.45;cursor:wait}
.hint{max-width:760px;margin:6px auto 0;color:var(--muted);font-size:12px}

@media (max-width:900px){
 .shell{grid-template-columns:1fr;grid-template-rows:auto auto}
 body{display:block}.shell{display:block}
 #thread{overflow:visible;padding:20px 16px}
 #index{border-left:0;border-top:1px solid var(--rule);padding:18px 0 18px 16px}
 header{padding:12px 16px;flex-wrap:wrap}header p{flex-basis:100%;order:3}
 .composer{position:sticky;bottom:0;padding:10px 16px 12px}
 .answer{padding:14px 16px}
}
</style></head><body>
<header>
 <h1>법령 근거 찾기</h1>
 <p>질문하면 법령·조례 원문에서 근거 조문을 찾아 답합니다. 허가 여부를 판정하지는 않습니다.</p>
 <button class="ghost" id="reset" type="button">새 대화</button>
</header>
<div class="shell">
 <main id="thread"><div class="col" id="log"></div></main>
 <aside id="index" aria-label="근거 색인">
  <h2>근거 색인</h2>
  <p id="indexNote">답에 쓰인 조문이 법령별로 모입니다. 누르면 도구가 읽은 원문(별표 포함)을 펼쳐 볼 수 있습니다.</p>
  <div id="laws"></div>
 </aside>
</div>
<div class="composer">
 <div class="col"><label for="q" class="sr" hidden>질문</label>
  <textarea id="q" rows="1" placeholder="예: 영천시 자연녹지지역에 단독주택 지을 때 건폐율은?"></textarea>
  <button class="send" id="send" type="button">물어보기</button></div>
 <div class="hint" id="hint">Enter로 보내고 Shift+Enter로 줄을 바꿉니다.</div>
</div>
<script>
const GROUPS=[
 ["땅과 건축",["경상북도 영천시 금호읍 신월리 1-51에 공장 지을 수 있어?","영천시 자연녹지지역에 일반음식점 낼 수 있어?","부산 해운대구 제1종일반주거지역에 카페 열 수 있어?","청주시 자연녹지지역 임야에 주유소 지으려면 근거 법령이랑 필요한 허가가 뭐야?"]],
 ["조문과 위임 관계",["건축법 제11조 건축허가에서 대통령령으로 정한 내용이 뭐야? 하위 법령 조문까지 찾아줘","국토계획법 시행령 제84조 건폐율 규정이 영천시 조례에서는 어떻게 정해져 있어?","영천시 도시계획 조례 제25조의 상위 법령 근거가 뭐야?"]],
 ["생활 속 법령",["혈중알코올농도 0.03%로 운전하면 어떤 처벌을 받아?","공직자에게 제공할 수 있는 음식물 가액 한도는 얼마야?"]]];
const TOOL={building_basis:"용도별 건축 근거",zone_ratios:"건폐율·용적률",parcel_zoning:"필지 조회",law_article:"조문 열람",law_search:"법령 검색",law_definition:"정의 찾기"};
const ARG={use:"용도",region:"지역",zone:"용도지역",address:"주소",land_category:"지목",law:"법령",article:"조문",query:"검색어",term:"용어",scope:"범위",chain:"방향",depth:"깊이",limit:"개수"};
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// 한글에 바로 붙은 **굵게** 는 CommonMark 규칙상 풀리지 않아 미리 바꾼다
const bold=s=>(s||"").replace(/\*\*([^*\n]+?)\*\*/g,"<strong>$1</strong>");
const md=s=>window.marked?marked.parse(bold(s)):"<p>"+esc(s).replace(/\n/g,"<br>")+"</p>";
let pending=null,conv={contextId:null,memory:null},busy=false;const index=new Map(),texts=new Map();

function welcome(){
 $("#log").innerHTML=`<section class="welcome"><h2>어느 법 몇 조에 그렇게 적혀 있는지 찾아 드립니다</h2>
 <p>주소, 지역과 용도지역, 법령 이름 중 아는 것으로 물어보세요. 답에는 근거 조문과 확인해야 할 조건이 함께 나옵니다.</p>
 ${GROUPS.map(([g,qs])=>`<div class="group"><h3>${esc(g)}</h3>${qs.map(q=>`<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`).join("")}</section>`;
 $("#log").querySelectorAll("[data-q]").forEach(b=>b.onclick=()=>{$("#q").value=b.dataset.q;grow();send()});
}
function renderIndex(){
 const box=$("#laws");box.innerHTML="";
 for(const [law,arts] of index){const d=document.createElement("div");d.className="law";
  d.innerHTML=`<h3>${esc(law)}</h3>`+[...arts].map(a=>{const src=textOf(law,a),ask=`<button type="button" class="ask" data-law="${esc(law)}" data-art="${esc(a)}">이 조문 다시 묻기</button>`;
   // 도구가 보여 준 원문이 있으면 펼쳐 읽게 하고, 없으면 다시 묻기만 둔다
   return src?`<details class="art"><summary class="tab">${esc(a)}</summary><div class="src">${esc(src)}</div>${ask}</details>`
    :`<button type="button" class="tab ask-only" data-law="${esc(law)}" data-art="${esc(a)}">${esc(a)}</button>`}).join("");box.appendChild(d)}
 box.querySelectorAll(".ask,.ask-only").forEach(t=>t.onclick=()=>{$("#q").value=`${t.dataset.law} ${t.dataset.art} 원문과 상위·하위 조문 보여줘`;grow();$("#q").focus()});
 $("#indexNote").hidden=index.size>0;
}
// 그 조문의 원문: 같은 조문 → 그 조문을 담은 상위(별표 2 ⊃ 별표 2 제1호) → 그 아래 조각 순으로 찾는다
function textOf(law,art){
 const m=texts.get(law);if(!m)return "";if(m.has(art))return m.get(art);
 const ks=[...m.keys()];const up=ks.filter(k=>art.startsWith(k)).sort((a,b)=>b.length-a.length)[0];if(up)return m.get(up);
 const down=ks.filter(k=>k.startsWith(art));return down.map(k=>m.get(k)).join("\n\n");
}
function addRefs(tools,answer){
 for(const t of tools)for(const [law,art,txt] of (t.ref_texts||[])){
  if(!law||!art||!txt)continue;if(!texts.has(law))texts.set(law,new Map());
  const m=texts.get(law);if((m.get(art)||"").length<txt.length)m.set(art,txt)}   // 같은 조문은 더 긴 글을 남긴다
 const flat=(answer||"").replace(/\s+/g,"");
 for(const t of tools)for(const [law,art] of (t.ref_pairs||[])){
  if(!law||!art||!flat.includes(law.replace(/\s+/g,""))||!flat.includes(art.replace(/\s+/g,"")))continue;   // 답에 나온 법령의, 답에 나온 조문만
  if(!index.has(law))index.set(law,new Set());index.get(law).add(art)}
 renderIndex();
}
function trail(tools){
 if(!tools.length)return "";
 return `<details class="trail"><summary>찾아본 과정 ${tools.length}단계</summary>${tools.map((t,i)=>{
  const args=Object.entries(t.args||{}).filter(([,v])=>v!==null&&v!==""&&v!==undefined).map(([k,v])=>`${ARG[k]||k} ${esc(v)}`).join(", ");
  return `<div class="step ${t.status==="ok"?"":"failed"}"><span class="n">${i+1}</span><span><b>${esc(TOOL[t.tool]||t.tool)}</b> <span class="args">${args}</span></span>${t.message?`<span class="msg">${esc(t.message)}</span>`:""}</div>`}).join("")}</details>`;
}
function grow(){const q=$("#q");q.style.height="auto";q.style.height=Math.min(q.scrollHeight,180)+"px"}
function setHint(){
 const n=conv.memory?.turns?.length||0;
 $("#hint").textContent=pending?"에이전트가 되물었습니다. 여기에 답하면 같은 질문을 이어서 찾습니다.":n?`같은 대화로 이어 묻습니다 (앞 질문 ${n}개 기억). 처음부터 하려면 새 대화를 누르세요.`:"Enter로 보내고 Shift+Enter로 줄을 바꿉니다.";
 $("#q").placeholder=pending?"되물은 내용에 답해 주세요":"예: 영천시 자연녹지지역에 단독주택 지을 때 건폐율은?";
}
async function send(){
 const text=$("#q").value.trim();if(!text||busy)return;busy=true;$("#send").disabled=true;
 if($(".welcome"))$("#log").innerHTML="";
 const turn=document.createElement("section");turn.className="turn";
 // 몇 번째 질문인지 구분선에 적는다 — 되물음에 답한 것도 한 턴이다
 const head=`<div class="sep">${$("#log").querySelectorAll(".turn").length+1}번째 질문</div><p class="q">${esc(text)}</p>`;
 turn.innerHTML=`${head}<div class="waiting" role="status"><i></i><span>원문에서 근거를 찾는 중 (0초)</span></div>`;
 $("#log").appendChild(turn);turn.scrollIntoView({block:"start",behavior:"smooth"});
 $("#q").value="";grow();
 const t0=Date.now(),timer=setInterval(()=>{const s=turn.querySelector(".waiting span");if(s)s.textContent=`원문에서 근거를 찾는 중 (${Math.round((Date.now()-t0)/1000)}초)`},1000);
 try{
  const r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text,contextId:conv.contextId,memory:conv.memory,...(pending||{})})});
  const d=await r.json();if(d.error)throw new Error(d.error);
  pending=d.state==="INPUT_REQUIRED"?{taskId:d.taskId,contextId:d.contextId}:null;
  conv.contextId=d.contextId||conv.contextId;if(d.memory)conv.memory=d.memory;
  const label={COMPLETED:"답변 완료",INPUT_REQUIRED:"되물음",FAILED:"실패",REJECTED:"범위 밖",CANCELED:"취소"}[d.state]||d.state;
  turn.innerHTML=`${head}<p class="meta"><span class="state ${d.state}">${label}</span>, ${d.seconds}초 걸림, 도구 ${d.tools.length}회</p>
   <div class="answer">${md(d.answer)}</div>${pending?`<p class="asking">아래 입력창에 답하면 이어서 찾습니다.</p>`:""}${trail(d.tools)}`;
  turn.querySelectorAll(".answer p").forEach(p=>{if(p.textContent.trim().startsWith("※ 다음 인용은"))p.className="unverified"});
  addRefs(d.tools,d.answer);
 }catch(e){turn.innerHTML=`${head}<p class="error">답을 받지 못했습니다: ${esc(e.message)}. 에이전트(:7016)가 켜져 있는지 확인한 뒤 다시 보내 주세요.</p>`;$("#q").value=text;grow()}
 finally{clearInterval(timer);busy=false;$("#send").disabled=false;setHint();$("#q").focus()}
}
$("#send").onclick=send;
$("#q").addEventListener("input",grow);
$("#q").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();send()}});
$("#reset").onclick=()=>{pending=null;conv={contextId:null,memory:null};index.clear();texts.clear();renderIndex();welcome();setHint();$("#q").focus()};
welcome();setHint();
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
