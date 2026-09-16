"""Self-contained human review page; decisions are exported, never auto-approved."""

import json
import os
from pathlib import Path

from .frozen_pilot import sha


def export_review(dataset, corpus, output):
    output = Path(output).resolve()
    if output.is_relative_to(corpus.root):
        raise ValueError("Review output must be outside baseline")
    documents = {
        key: {
            "title": doc["title"],
            "url": os.path.relpath(
                corpus.root / f"pdfs/{doc['sha256']}.pdf", output.parent
            ).replace("\\", "/"),
        }
        for key, doc in corpus.documents.items()
    }
    payload = json.dumps(
        {"dataset": dataset, "dataset_sha256": sha(dataset), "documents": documents},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    page = TEMPLATE.replace("__PAYLOAD__", payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>48 篇论文 · 20 题测评审核</title><style>
:root{font-family:"Microsoft YaHei",system-ui,sans-serif;color:#1d2c40;background:#f2f5fa;line-height:1.65}
*{box-sizing:border-box}body{margin:0}header{background:#152b44;color:white;padding:25px max(24px,calc((100vw - 1160px)/2))}
h1{font-size:26px;margin:0 0 6px}header p{margin:5px 0;color:#d2e1f1}.wrap{max-width:1160px;margin:auto;padding:22px}
.bar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:10px 0 20px}input,select,button,textarea{font:inherit;border:1px solid #bccad9;border-radius:7px;padding:8px 12px;background:white}input[type=checkbox]{width:18px;height:18px}button{cursor:pointer}button:hover{background:#edf3fc}button:disabled{opacity:.45;cursor:default}.primary{background:#2263aa;color:white}.primary:hover{background:#184e87}
.card{background:white;border:1px solid #dbe3ed;border-radius:12px;margin:18px 0;padding:24px}.muted{color:#596d83;font-size:14px}.badges{display:flex;gap:8px;flex-wrap:wrap}.badge{background:#edf3fa;border-radius:20px;padding:3px 11px;font-size:13px}.warning{background:#fff4da;color:#795414}.approved{background:#e0f3e8;color:#165f3e}.rejected{background:#fbe5e7;color:#9e293a}h2{font-size:21px;line-height:1.65;margin:14px 0}h3{font-size:16px;margin:0 0 6px}.answer{border-left:4px solid #4d80b2;background:#f3f7fc;padding:13px 16px;margin:18px 0}.evidence{border:1px solid #d6e0ea;border-radius:8px;padding:16px;margin:12px 0}.quote{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.75 Georgia,"Microsoft YaHei",serif;color:#28384d;background:#f7f9fc;padding:12px;border-radius:5px}.id{font-family:Consolas,monospace;overflow-wrap:anywhere;font-size:12px}summary{cursor:pointer;color:#24609b}.decision{border-top:1px solid #e0e6ec;padding-top:16px;margin-top:16px;display:grid;gap:12px}.check{display:flex;align-items:center;gap:8px}textarea{width:100%;min-height:70px}a{color:#24609b}.note{font-size:14px;background:#fff7e4;padding:12px;border-radius:8px}.empty{padding:60px;text-align:center}#search{flex:1;min-width:160px}
</style></head><body>
<header><h1>48 篇论文 · 首批 20 题测评审核</h1><p>基线 papers48-20260916 · 48 篇 / 993 分块 · 14 中文 / 6 英文 · 全部为开发集</p><p>概念机制 5 · 精确实体 3 · 方法条件 5 · 结果与局限 4 · 跨论文 3</p></header>
<main class="wrap"><div class="note">这些题目由助手依据原文编写，尚未经人工确认。程序校验只证明片段及位置存在；请检查问题是否自然、答案是否完整、证据是否充分。PDF 部分匹配提示需要重点核对。审核决定保存在当前浏览器，下载后才可导入测评器。</div>
<div class="bar"><label>审核人 <input id="reviewer" placeholder="输入姓名或代号"></label><button class="primary" id="export">下载审核记录 JSON</button><span id="progress"></span></div>
<div class="bar"><input id="search" placeholder="搜索问题 / 答案 / 编号"><select id="status"><option value="all">全部状态</option><option value="pending">待审核</option><option value="approved">已通过</option><option value="rejected">需修改</option></select><select id="kind"><option value="all">全部题型</option></select><select id="language"><option value="all">全部语言</option><option value="zh">中文</option><option value="en">英文</option></select></div>
<div id="cards"></div><p class="muted">评分范围：文档命中、目标文档召回、必需证据组召回、证据齐全率和 TargetRR。普通 MRR / nDCG 需要额外的相关性标注；本批不含无答案、Handoff 或生成答案评分。</p></main>
<script type="application/json" id="payload">__PAYLOAD__</script>
<script>
const payload=JSON.parse(document.querySelector('#payload').textContent), cases=payload.dataset.cases;
const labels={mechanism:'概念机制',precise_entity:'精确实体',method_condition:'方法条件',result:'结果与局限',cross_paper:'跨论文'};
const names={pending:'待审核',approved:'已通过',rejected:'需修改'};
const key='papers48-review-'+payload.dataset_sha256;
let reviews=Object.fromEntries(cases.map(c=>[c.id,{case_id:c.id,...c.review}]));
try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved){reviews=saved.reviews;document.querySelector('#reviewer').value=saved.reviewer||'';}}catch(e){}
function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
function persist(){try{localStorage.setItem(key,JSON.stringify({reviews,reviewer:document.querySelector('#reviewer').value}));}catch(e){alert('浏览器存储不可用，请及时下载审核记录。');}}
Object.entries(labels).forEach(([k,v])=>{const o=el('option',v);o.value=k;document.querySelector('#kind').append(o);});
function render(){
 const holder=document.querySelector('#cards');holder.replaceChildren();
 const status=document.querySelector('#status').value,kind=document.querySelector('#kind').value,lang=document.querySelector('#language').value,search=document.querySelector('#search').value.toLowerCase();
 const visible=cases.filter(c=>(status==='all'||reviews[c.id].status===status)&&(kind==='all'||c.query_type===kind)&&(lang==='all'||c.language===lang)&&[c.id,c.query,c.reference_answer].join(' ').toLowerCase().includes(search));
 document.querySelector('#progress').textContent=`已通过 ${Object.values(reviews).filter(r=>r.status==='approved').length} / 20 · 当前显示 ${visible.length} 题`;
 if(!visible.length)holder.append(el('div','没有符合条件的题目','empty'));
 visible.forEach(c=>{
  const r=reviews[c.id],card=el('article',undefined,'card');card.id=c.id;
  const badges=el('div',undefined,'badges');[c.id,labels[c.query_type],c.language==='zh'?'中文':'英文'].forEach(t=>badges.append(el('span',t,'badge')));badges.append(el('span',names[r.status],'badge '+r.status));card.append(badges,el('h2',c.query));
  const answer=el('div',undefined,'answer');answer.append(el('h3','参考答案（待审核）'),el('div',c.reference_answer));card.append(answer);
  c.evidence_groups.forEach(g=>{g.alternatives.forEach(plan=>{plan.forEach(s=>{
   const box=el('section',undefined,'evidence'),doc=payload.documents[s.document_id];box.append(el('h3',g.id+' · '+g.purpose),el('div',doc.title),el('div','章节：'+s.section,'muted'),el('div',s.chunk_id+' · 字符 ['+s.start+', '+s.end+')','id'),el('div',s.quote,'quote'));
   if(s.pdf_source){const src=s.pdf_source,a=el('a','打开原始 PDF · 物理第 '+src.physical_page+' 页');a.href=doc.url+'#page='+src.physical_page;a.target='_blank';a.rel='noopener';box.append(a);
    const detail=el('details');detail.append(el('summary',src.alignment==='exact_normalized'?'PDF 原文完整匹配（规范化空白后）':'PDF 片段部分匹配 · 请核对完整依据（连续匹配 '+Math.round(src.matched_fraction*100)+'%）'),el('div',src.quote,'quote'));box.append(detail);
   }else{box.append(el('div','PDF 页面未自动定位，需要人工查找。','note'));}card.append(box);
  });});});
  const decision=el('div',undefined,'decision'),label=el('label',undefined,'check'),check=el('input');check.type='checkbox';check.checked=!!r.source_checked;label.append(check,el('span','我已核对原文，问题、参考答案与必需证据一致'));decision.append(label);
  const note=el('textarea');note.placeholder='审核备注：遗漏证据、表述问题、答案边界……';note.value=r.notes||'';note.oninput=()=>{r.notes=note.value;persist();};decision.append(note);
  const buttons=el('div',undefined,'bar');[['approved','通过'],['rejected','需修改'],['pending','恢复待审核']].forEach(([state,title])=>{const b=el('button',title,state==='approved'?'primary':'');b.onclick=()=>{const reviewer=document.querySelector('#reviewer').value.trim();if(state!=='pending'&&!reviewer){alert('请先填写审核人。');return;}if(state==='approved'&&!check.checked){alert('请核对原文后勾选确认。');return;}Object.assign(r,{status:state,reviewer:state==='pending'?null:reviewer,reviewed_at:state==='pending'?null:new Date().toISOString(),source_checked:check.checked,notes:note.value});persist();render();};buttons.append(b);});
  check.onchange=()=>{r.source_checked=check.checked;if(!check.checked&&r.status==='approved'){r.status='pending';r.reviewed_at=null;r.reviewer=null;}persist();render();};decision.append(buttons);card.append(decision);holder.append(card);
 });
}
['search','status','kind','language'].forEach(id=>document.getElementById(id).addEventListener('input',render));document.querySelector('#reviewer').addEventListener('input',persist);
document.querySelector('#export').onclick=()=>{persist();const out={dataset_sha256:payload.dataset_sha256,exported_at:new Date().toISOString(),reviews:Object.values(reviews)};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'}));a.download='papers48-pilot20-reviews.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};render();
</script></body></html>"""
