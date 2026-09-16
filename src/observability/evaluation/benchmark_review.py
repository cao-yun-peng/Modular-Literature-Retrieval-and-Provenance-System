"""Offline human relevance review; grades can only be entered by the reviewer."""

from __future__ import annotations

import json
from pathlib import Path


def export_judgments(
    data: dict, corpus: dict, index: dict, judgments: dict, root: Path, target: Path
) -> None:
    payload = {
        "dataset": data,
        "judgments": judgments,
        "chunks": [
            {
                "chunk_id": c["chunk_id"],
                "paper_id": c["paper_id"],
                "text": c["text"],
                "metadata": {k: c["metadata"].get(k) for k in ("page_start", "page_end")},
            }
            for c in index["chunks"]
        ],
        "papers": {
            p["paper_id"]: {"title": p["title"], "url": (root / p["pdf_file"]).resolve().as_uri()}
            for p in corpus["papers"]
            if p.get("pdf_file")
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    page = r"""<!doctype html><meta charset="utf-8"><title>论文证据相关性标注</title>
<style>body{font:16px/1.65 system-ui;color:#192c40;background:#f4f6f8;max-width:1100px;margin:24px auto}header{position:sticky;top:0;background:#dceafa;padding:16px;z-index:2}article,section{background:white;padding:20px;margin:16px 0;border:1px solid #cad5df;border-radius:8px}button,input,select{font:inherit;padding:7px}input{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}small{overflow-wrap:anywhere}button{cursor:pointer}#query{font-weight:600}label{margin-right:12px}</style>
<h1>论文证据相关性标注</h1><p>直接支持＝2；仅背景相关＝1；不相关＝0。请核验原 PDF，保留所有检索候选，不能将未审核项默认记为 0。搜索下方完整索引可以补充遗漏证据；若解析丢失证据，应修正题目的原文标注并在诊断记录中保留缺失。本页面不会上传数据。</p>
<header><label>审核人 <input id="reviewer"></label><label>标签版本 <input id="version" value="review-1"></label><button id="save">导出标签 JSON</button><p><select id="case"></select> <span id="progress"></span></p></header>
<section><p id="query"></p><pre id="answer"></pre><div id="sources"></div></section>
<div id="candidates"></div><section><h2>搜索完整索引并补充遗漏证据</h2><input id="search" placeholder="输入论文术语或短语"><button id="find">搜索</button><p>最多展示 100 个匹配块；搜索范围包括未进入三路 Top-20 的内容。</p><div id="matches"></div></section>
<script>const state=PAYLOAD;const $=id=>document.getElementById(id);const chunks=new Map(state.chunks.map(c=>[c.chunk_id,c]));
const cases=state.dataset.test_cases.filter(c=>Object.hasOwn(state.judgments.cases,c.id));
for(const c of cases){const option=document.createElement('option');option.value=c.id;option.textContent=c.id+' · '+c.language+' · '+c.query_type;$('case').append(option);}
function el(tag,text){const node=document.createElement(tag);node.textContent=text;return node;}
function pdf(parent,pid,page){const paper=state.papers[pid];if(!paper)return;const a=el('a',paper.title+(page?' · PDF '+page:''));a.href=paper.url+(Number.isInteger(page)?'#page='+page:'');a.target='_blank';parent.append(a);}
function progress(){const rows=Object.values(state.judgments.cases[$('case').value]||{});$('progress').textContent=rows.filter(r=>Number.isInteger(r.grade)&&r.reviewer&&r.reviewed_at).length+' / '+rows.length+' 已标注';}
function card(cid,row,add=false){const chunk=chunks.get(cid);const article=document.createElement('article');pdf(article,row.paper_id,chunk?.metadata?.page_start);article.append(el('small','\n'+cid));article.append(el('pre',row.text));
if(add){const button=el('button','加入本题候选');button.onclick=()=>{state.judgments.cases[$('case').value][cid]={grade:null,reviewer:null,reviewed_at:null,text:row.text,paper_id:row.paper_id,retrievers:['human-search']};render();};article.append(button);return article;}
const select=document.createElement('select');for(const [value,label] of [['','未审核'],['2','2 · 直接支持'],['1','1 · 背景相关'],['0','0 · 不相关']]){const option=el('option',label);option.value=value;select.append(option);}select.value=row.grade===null?'':String(row.grade);select.onchange=()=>{const reviewer=$('reviewer').value.trim();if(!reviewer){alert('请先填写真实审核人标识');select.value=row.grade===null?'':String(row.grade);return;}row.grade=select.value===''?null:Number(select.value);row.reviewer=row.grade===null?null:reviewer;row.reviewed_at=row.grade===null?null:new Date().toISOString();progress();};article.append(select);return article;}
function render(){const c=cases.find(c=>c.id===$('case').value);if(!c)return;$('query').textContent=c.query;$('answer').textContent=JSON.stringify(c.reference_answer,null,2);$('sources').replaceChildren();for(const pid of c.source_paper_ids){pdf($('sources'),pid);$('sources').append(el('br',''));}$('candidates').replaceChildren();for(const [cid,row] of Object.entries(state.judgments.cases[c.id]))$('candidates').append(card(cid,row));$('matches').replaceChildren();progress();}
$('case').onchange=render;$('find').onclick=()=>{const query=$('search').value.trim().toLowerCase();if(query.length<3){alert('请输入至少 3 个字符');return;}const pool=state.judgments.cases[$('case').value];$('matches').replaceChildren();for(const c of state.chunks.filter(c=>!Object.hasOwn(pool,c.chunk_id)&&c.text.toLowerCase().includes(query)).slice(0,100))$('matches').append(card(c.chunk_id,c,true));};
$('save').onclick=()=>{const version=$('version').value.trim();if(!version){alert('请填写新标签版本');return;}state.judgments.version=version;state.judgments.updated_at=new Date().toISOString();const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(state.judgments,null,2)],{type:'application/json'}));a.download='judgments-'+version+'.json';a.click();URL.revokeObjectURL(a.href);};render();</script>"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page.replace("PAYLOAD", encoded), encoding="utf-8")
