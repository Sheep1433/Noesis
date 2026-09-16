"""多源会话轨迹查看器：统一 JSON 契约 + 可插拔 Provider，本地 HTTP 服务 + 单页 HTML。

用法（backend/ 下）:
    # 裸命令即全量加载：noesis（Postgres 直连）必挂，
    # 检测到 ~/.codex/sessions 自动挂 codex，浏览器自动打开
    uv run python ../.agents/skills/noesis-run-trace-analysis/references/session_viewer.py

    # 额外源（可叠加）
    uv run python .../session_viewer.py \
        --opencode /path/to/opencode.db \
        --codex /path/to/other_sessions

数据源（Provider）：
  noesis    Postgres 直连（t_chat_session / t_chat_message，usage 来自 extra.usage）
  opencode  SQLite .db（session / message / part 表，即 trace_view.html 的库）
  codex     rollout JSONL 目录（~/.codex/sessions，按日期子目录扫描）

扩展新源：实现 list_sessions() / get_messages(session_id) 返回统一契约，
注册进 PROVIDERS 即可（见 Provider 基类 docstring）。

统一契约（所有 Provider 归一化到同一形状）：
  session  {id, parent_id, title, created_at(ms), updated_at(ms), kind, n_msgs,
            input_tokens, output_tokens, tool_count}
  messages {session_id, title, messages: [{id, role, origin,
              items: [{kind: text|reasoning|tool|usage, ...}]}],
            stats: {tool_count, reasoning_count, input_tokens, ...}}
  item:
    text     {kind, text}
    reasoning{kind, text}
    tool     {kind, tool, input, output}
    usage    {kind, usage: {steps, input_tokens, output_tokens,
             cache_read_tokens, llm_ms, ttft_ms}}
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import re
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SKILL_REF_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------- HTML（单页客户端，多源）

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session Trace Viewer</title>
<script>
/**
 * marked v12.0.2 - a markdown parser
 * Copyright (c) 2011-2024, Christopher Jeffrey. (MIT Licensed)
 * https://github.com/markedjs/marked
 */
!function(e,t){"object"==typeof exports&&"undefined"!=typeof module?t(exports):"function"==typeof define&&define.amd?define(["exports"],t):t((e="undefined"!=typeof globalThis?globalThis:e||self).marked={})}(this,(function(e){"use strict";function t(){return{async:!1,breaks:!1,extensions:null,gfm:!0,hooks:null,pedantic:!1,renderer:null,silent:!1,tokenizer:null,walkTokens:null}}function n(t){e.defaults=t}e.defaults={async:!1,breaks:!1,extensions:null,gfm:!0,hooks:null,pedantic:!1,renderer:null,silent:!1,tokenizer:null,walkTokens:null};const s=/[&<>"']/,r=new RegExp(s.source,"g"),i=/[<>"']|&(?!(#\d{1,7}|#[Xx][a-fA-F0-9]{1,6}|\w+);)/,l=new RegExp(i.source,"g"),o={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"},a=e=>o[e];function c(e,t){if(t){if(s.test(e))return e.replace(r,a)}else if(i.test(e))return e.replace(l,a);return e}const h=/&(#(?:\d+)|(?:#x[0-9A-Fa-f]+)|(?:\w+));?/gi;function p(e){return e.replace(h,((e,t)=>"colon"===(t=t.toLowerCase())?":":"#"===t.charAt(0)?"x"===t.charAt(1)?String.fromCharCode(parseInt(t.substring(2),16)):String.fromCharCode(+t.substring(1)):""))}const u=/(^|[^\[])\^/g;function k(e,t){let n="string"==typeof e?e:e.source;t=t||"";const s={replace:(e,t)=>{let r="string"==typeof t?t:t.source;return r=r.replace(u,"$1"),n=n.replace(e,r),s},getRegex:()=>new RegExp(n,t)};return s}function g(e){try{e=encodeURI(e).replace(/%25/g,"%")}catch(e){return null}return e}const f={exec:()=>null};function d(e,t){const n=e.replace(/\|/g,((e,t,n)=>{let s=!1,r=t;for(;--r>=0&&"\\"===n[r];)s=!s;return s?"|":" |"})).split(/ \|/);let s=0;if(n[0].trim()||n.shift(),n.length>0&&!n[n.length-1].trim()&&n.pop(),t)if(n.length>t)n.splice(t);else for(;n.length<t;)n.push("");for(;s<n.length;s++)n[s]=n[s].trim().replace(/\\\|/g,"|");return n}function x(e,t,n){const s=e.length;if(0===s)return"";let r=0;for(;r<s;){const i=e.charAt(s-r-1);if(i!==t||n){if(i===t||!n)break;r++}else r++}return e.slice(0,s-r)}function b(e,t,n,s){const r=t.href,i=t.title?c(t.title):null,l=e[1].replace(/\\([\[\]])/g,"$1");if("!"!==e[0].charAt(0)){s.state.inLink=!0;const e={type:"link",raw:n,href:r,title:i,text:l,tokens:s.inlineTokens(l)};return s.state.inLink=!1,e}return{type:"image",raw:n,href:r,title:i,text:c(l)}}class w{options;rules;lexer;constructor(t){this.options=t||e.defaults}space(e){const t=this.rules.block.newline.exec(e);if(t&&t[0].length>0)return{type:"space",raw:t[0]}}code(e){const t=this.rules.block.code.exec(e);if(t){const e=t[0].replace(/^ {1,4}/gm,"");return{type:"code",raw:t[0],codeBlockStyle:"indented",text:this.options.pedantic?e:x(e,"\n")}}}fences(e){const t=this.rules.block.fences.exec(e);if(t){const e=t[0],n=function(e,t){const n=e.match(/^(\s+)(?:```)/);if(null===n)return t;const s=n[1];return t.split("\n").map((e=>{const t=e.match(/^\s+/);if(null===t)return e;const[n]=t;return n.length>=s.length?e.slice(s.length):e})).join("\n")}(e,t[3]||"");return{type:"code",raw:e,lang:t[2]?t[2].trim().replace(this.rules.inline.anyPunctuation,"$1"):t[2],text:n}}}heading(e){const t=this.rules.block.heading.exec(e);if(t){let e=t[2].trim();if(/#$/.test(e)){const t=x(e,"#");this.options.pedantic?e=t.trim():t&&!/ $/.test(t)||(e=t.trim())}return{type:"heading",raw:t[0],depth:t[1].length,text:e,tokens:this.lexer.inline(e)}}}hr(e){const t=this.rules.block.hr.exec(e);if(t)return{type:"hr",raw:t[0]}}blockquote(e){const t=this.rules.block.blockquote.exec(e);if(t){let e=t[0].replace(/\n {0,3}((?:=+|-+) *)(?=\n|$)/g,"\n    $1");e=x(e.replace(/^ *>[ \t]?/gm,""),"\n");const n=this.lexer.state.top;this.lexer.state.top=!0;const s=this.lexer.blockTokens(e);return this.lexer.state.top=n,{type:"blockquote",raw:t[0],tokens:s,text:e}}}list(e){let t=this.rules.block.list.exec(e);if(t){let n=t[1].trim();const s=n.length>1,r={type:"list",raw:"",ordered:s,start:s?+n.slice(0,-1):"",loose:!1,items:[]};n=s?`\\d{1,9}\\${n.slice(-1)}`:`\\${n}`,this.options.pedantic&&(n=s?n:"[*+-]");const i=new RegExp(`^( {0,3}${n})((?:[\t ][^\\n]*)?(?:\\n|$))`);let l="",o="",a=!1;for(;e;){let n=!1;if(!(t=i.exec(e)))break;if(this.rules.block.hr.test(e))break;l=t[0],e=e.substring(l.length);let s=t[2].split("\n",1)[0].replace(/^\t+/,(e=>" ".repeat(3*e.length))),c=e.split("\n",1)[0],h=0;this.options.pedantic?(h=2,o=s.trimStart()):(h=t[2].search(/[^ ]/),h=h>4?1:h,o=s.slice(h),h+=t[1].length);let p=!1;if(!s&&/^ *$/.test(c)&&(l+=c+"\n",e=e.substring(c.length+1),n=!0),!n){const t=new RegExp(`^ {0,${Math.min(3,h-1)}}(?:[*+-]|\\d{1,9}[.)])((?:[ \t][^\\n]*)?(?:\\n|$))`),n=new RegExp(`^ {0,${Math.min(3,h-1)}}((?:- *){3,}|(?:_ *){3,}|(?:\\* *){3,})(?:\\n+|$)`),r=new RegExp(`^ {0,${Math.min(3,h-1)}}(?:\`\`\`|~~~)`),i=new RegExp(`^ {0,${Math.min(3,h-1)}}#`);for(;e;){const a=e.split("\n",1)[0];if(c=a,this.options.pedantic&&(c=c.replace(/^ {1,4}(?=( {4})*[^ ])/g,"  ")),r.test(c))break;if(i.test(c))break;if(t.test(c))break;if(n.test(e))break;if(c.search(/[^ ]/)>=h||!c.trim())o+="\n"+c.slice(h);else{if(p)break;if(s.search(/[^ ]/)>=4)break;if(r.test(s))break;if(i.test(s))break;if(n.test(s))break;o+="\n"+c}p||c.trim()||(p=!0),l+=a+"\n",e=e.substring(a.length+1),s=c.slice(h)}}r.loose||(a?r.loose=!0:/\n *\n *$/.test(l)&&(a=!0));let u,k=null;this.options.gfm&&(k=/^\[[ xX]\] /.exec(o),k&&(u="[ ] "!==k[0],o=o.replace(/^\[[ xX]\] +/,""))),r.items.push({type:"list_item",raw:l,task:!!k,checked:u,loose:!1,text:o,tokens:[]}),r.raw+=l}r.items[r.items.length-1].raw=l.trimEnd(),r.items[r.items.length-1].text=o.trimEnd(),r.raw=r.raw.trimEnd();for(let e=0;e<r.items.length;e++)if(this.lexer.state.top=!1,r.items[e].tokens=this.lexer.blockTokens(r.items[e].text,[]),!r.loose){const t=r.items[e].tokens.filter((e=>"space"===e.type)),n=t.length>0&&t.some((e=>/\n.*\n/.test(e.raw)));r.loose=n}if(r.loose)for(let e=0;e<r.items.length;e++)r.items[e].loose=!0;return r}}html(e){const t=this.rules.block.html.exec(e);if(t){return{type:"html",block:!0,raw:t[0],pre:"pre"===t[1]||"script"===t[1]||"style"===t[1],text:t[0]}}}def(e){const t=this.rules.block.def.exec(e);if(t){const e=t[1].toLowerCase().replace(/\s+/g," "),n=t[2]?t[2].replace(/^<(.*)>$/,"$1").replace(this.rules.inline.anyPunctuation,"$1"):"",s=t[3]?t[3].substring(1,t[3].length-1).replace(this.rules.inline.anyPunctuation,"$1"):t[3];return{type:"def",tag:e,raw:t[0],href:n,title:s}}}table(e){const t=this.rules.block.table.exec(e);if(!t)return;if(!/[:|]/.test(t[2]))return;const n=d(t[1]),s=t[2].replace(/^\||\| *$/g,"").split("|"),r=t[3]&&t[3].trim()?t[3].replace(/\n[ \t]*$/,"").split("\n"):[],i={type:"table",raw:t[0],header:[],align:[],rows:[]};if(n.length===s.length){for(const e of s)/^ *-+: *$/.test(e)?i.align.push("right"):/^ *:-+: *$/.test(e)?i.align.push("center"):/^ *:-+ *$/.test(e)?i.align.push("left"):i.align.push(null);for(const e of n)i.header.push({text:e,tokens:this.lexer.inline(e)});for(const e of r)i.rows.push(d(e,i.header.length).map((e=>({text:e,tokens:this.lexer.inline(e)}))));return i}}lheading(e){const t=this.rules.block.lheading.exec(e);if(t)return{type:"heading",raw:t[0],depth:"="===t[2].charAt(0)?1:2,text:t[1],tokens:this.lexer.inline(t[1])}}paragraph(e){const t=this.rules.block.paragraph.exec(e);if(t){const e="\n"===t[1].charAt(t[1].length-1)?t[1].slice(0,-1):t[1];return{type:"paragraph",raw:t[0],text:e,tokens:this.lexer.inline(e)}}}text(e){const t=this.rules.block.text.exec(e);if(t)return{type:"text",raw:t[0],text:t[0],tokens:this.lexer.inline(t[0])}}escape(e){const t=this.rules.inline.escape.exec(e);if(t)return{type:"escape",raw:t[0],text:c(t[1])}}tag(e){const t=this.rules.inline.tag.exec(e);if(t)return!this.lexer.state.inLink&&/^<a /i.test(t[0])?this.lexer.state.inLink=!0:this.lexer.state.inLink&&/^<\/a>/i.test(t[0])&&(this.lexer.state.inLink=!1),!this.lexer.state.inRawBlock&&/^<(pre|code|kbd|script)(\s|>)/i.test(t[0])?this.lexer.state.inRawBlock=!0:this.lexer.state.inRawBlock&&/^<\/(pre|code|kbd|script)(\s|>)/i.test(t[0])&&(this.lexer.state.inRawBlock=!1),{type:"html",raw:t[0],inLink:this.lexer.state.inLink,inRawBlock:this.lexer.state.inRawBlock,block:!1,text:t[0]}}link(e){const t=this.rules.inline.link.exec(e);if(t){const e=t[2].trim();if(!this.options.pedantic&&/^</.test(e)){if(!/>$/.test(e))return;const t=x(e.slice(0,-1),"\\");if((e.length-t.length)%2==0)return}else{const e=function(e,t){if(-1===e.indexOf(t[1]))return-1;let n=0;for(let s=0;s<e.length;s++)if("\\"===e[s])s++;else if(e[s]===t[0])n++;else if(e[s]===t[1]&&(n--,n<0))return s;return-1}(t[2],"()");if(e>-1){const n=(0===t[0].indexOf("!")?5:4)+t[1].length+e;t[2]=t[2].substring(0,e),t[0]=t[0].substring(0,n).trim(),t[3]=""}}let n=t[2],s="";if(this.options.pedantic){const e=/^([^'"]*[^\s])\s+(['"])(.*)\2/.exec(n);e&&(n=e[1],s=e[3])}else s=t[3]?t[3].slice(1,-1):"";return n=n.trim(),/^</.test(n)&&(n=this.options.pedantic&&!/>$/.test(e)?n.slice(1):n.slice(1,-1)),b(t,{href:n?n.replace(this.rules.inline.anyPunctuation,"$1"):n,title:s?s.replace(this.rules.inline.anyPunctuation,"$1"):s},t[0],this.lexer)}}reflink(e,t){let n;if((n=this.rules.inline.reflink.exec(e))||(n=this.rules.inline.nolink.exec(e))){const e=t[(n[2]||n[1]).replace(/\s+/g," ").toLowerCase()];if(!e){const e=n[0].charAt(0);return{type:"text",raw:e,text:e}}return b(n,e,n[0],this.lexer)}}emStrong(e,t,n=""){let s=this.rules.inline.emStrongLDelim.exec(e);if(!s)return;if(s[3]&&n.match(/[\p{L}\p{N}]/u))return;if(!(s[1]||s[2]||"")||!n||this.rules.inline.punctuation.exec(n)){const n=[...s[0]].length-1;let r,i,l=n,o=0;const a="*"===s[0][0]?this.rules.inline.emStrongRDelimAst:this.rules.inline.emStrongRDelimUnd;for(a.lastIndex=0,t=t.slice(-1*e.length+n);null!=(s=a.exec(t));){if(r=s[1]||s[2]||s[3]||s[4]||s[5]||s[6],!r)continue;if(i=[...r].length,s[3]||s[4]){l+=i;continue}if((s[5]||s[6])&&n%3&&!((n+i)%3)){o+=i;continue}if(l-=i,l>0)continue;i=Math.min(i,i+l+o);const t=[...s[0]][0].length,a=e.slice(0,n+s.index+t+i);if(Math.min(n,i)%2){const e=a.slice(1,-1);return{type:"em",raw:a,text:e,tokens:this.lexer.inlineTokens(e)}}const c=a.slice(2,-2);return{type:"strong",raw:a,text:c,tokens:this.lexer.inlineTokens(c)}}}}codespan(e){const t=this.rules.inline.code.exec(e);if(t){let e=t[2].replace(/\n/g," ");const n=/[^ ]/.test(e),s=/^ /.test(e)&&/ $/.test(e);return n&&s&&(e=e.substring(1,e.length-1)),e=c(e,!0),{type:"codespan",raw:t[0],text:e}}}br(e){const t=this.rules.inline.br.exec(e);if(t)return{type:"br",raw:t[0]}}del(e){const t=this.rules.inline.del.exec(e);if(t)return{type:"del",raw:t[0],text:t[2],tokens:this.lexer.inlineTokens(t[2])}}autolink(e){const t=this.rules.inline.autolink.exec(e);if(t){let e,n;return"@"===t[2]?(e=c(t[1]),n="mailto:"+e):(e=c(t[1]),n=e),{type:"link",raw:t[0],text:e,href:n,tokens:[{type:"text",raw:e,text:e}]}}}url(e){let t;if(t=this.rules.inline.url.exec(e)){let e,n;if("@"===t[2])e=c(t[0]),n="mailto:"+e;else{let s;do{s=t[0],t[0]=this.rules.inline._backpedal.exec(t[0])?.[0]??""}while(s!==t[0]);e=c(t[0]),n="www."===t[1]?"http://"+t[0]:t[0]}return{type:"link",raw:t[0],text:e,href:n,tokens:[{type:"text",raw:e,text:e}]}}}inlineText(e){const t=this.rules.inline.text.exec(e);if(t){let e;return e=this.lexer.state.inRawBlock?t[0]:c(t[0]),{type:"text",raw:t[0],text:e}}}}const m=/^ {0,3}((?:-[\t ]*){3,}|(?:_[ \t]*){3,}|(?:\*[ \t]*){3,})(?:\n+|$)/,y=/(?:[*+-]|\d{1,9}[.)])/,$=k(/^(?!bull |blockCode|fences|blockquote|heading|html)((?:.|\n(?!\s*?\n|bull |blockCode|fences|blockquote|heading|html))+?)\n {0,3}(=+|-+) *(?:\n+|$)/).replace(/bull/g,y).replace(/blockCode/g,/ {4}/).replace(/fences/g,/ {0,3}(?:`{3,}|~{3,})/).replace(/blockquote/g,/ {0,3}>/).replace(/heading/g,/ {0,3}#{1,6}/).replace(/html/g,/ {0,3}<[^\n>]+>\n/).getRegex(),z=/^([^\n]+(?:\n(?!hr|heading|lheading|blockquote|fences|list|html|table| +\n)[^\n]+)*)/,T=/(?!\s*\])(?:\\.|[^\[\]\\])+/,R=k(/^ {0,3}\[(label)\]: *(?:\n *)?([^<\s][^\s]*|<.*?>)(?:(?: +(?:\n *)?| *\n *)(title))? *(?:\n+|$)/).replace("label",T).replace("title",/(?:"(?:\\"?|[^"\\])*"|'[^'\n]*(?:\n[^'\n]+)*\n?'|\([^()]*\))/).getRegex(),_=k(/^( {0,3}bull)([ \t][^\n]+?)?(?:\n|$)/).replace(/bull/g,y).getRegex(),A="address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|meta|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul",S=/<!--(?:-?>|[\s\S]*?(?:-->|$))/,I=k("^ {0,3}(?:<(script|pre|style|textarea)[\\s>][\\s\\S]*?(?:</\\1>[^\\n]*\\n+|$)|comment[^\\n]*(\\n+|$)|<\\?[\\s\\S]*?(?:\\?>\\n*|$)|<![A-Z][\\s\\S]*?(?:>\\n*|$)|<!\\[CDATA\\[[\\s\\S]*?(?:\\]\\]>\\n*|$)|</?(tag)(?: +|\\n|/?>)[\\s\\S]*?(?:(?:\\n *)+\\n|$)|<(?!script|pre|style|textarea)([a-z][\\w-]*)(?:attribute)*? */?>(?=[ \\t]*(?:\\n|$))[\\s\\S]*?(?:(?:\\n *)+\\n|$)|</(?!script|pre|style|textarea)[a-z][\\w-]*\\s*>(?=[ \\t]*(?:\\n|$))[\\s\\S]*?(?:(?:\\n *)+\\n|$))","i").replace("comment",S).replace("tag",A).replace("attribute",/ +[a-zA-Z:_][\w.:-]*(?: *= *"[^"\n]*"| *= *'[^'\n]*'| *= *[^\s"'=<>`]+)?/).getRegex(),E=k(z).replace("hr",m).replace("heading"," {0,3}#{1,6}(?:\\s|$)").replace("|lheading","").replace("|table","").replace("blockquote"," {0,3}>").replace("fences"," {0,3}(?:`{3,}(?=[^`\\n]*\\n)|~{3,})[^\\n]*\\n").replace("list"," {0,3}(?:[*+-]|1[.)]) ").replace("html","</?(?:tag)(?: +|\\n|/?>)|<(?:script|pre|style|textarea|!--)").replace("tag",A).getRegex(),q={blockquote:k(/^( {0,3}> ?(paragraph|[^\n]*)(?:\n|$))+/).replace("paragraph",E).getRegex(),code:/^( {4}[^\n]+(?:\n(?: *(?:\n|$))*)?)+/,def:R,fences:/^ {0,3}(`{3,}(?=[^`\n]*(?:\n|$))|~{3,})([^\n]*)(?:\n|$)(?:|([\s\S]*?)(?:\n|$))(?: {0,3}\1[~`]* *(?=\n|$)|$)/,heading:/^ {0,3}(#{1,6})(?=\s|$)(.*)(?:\n+|$)/,hr:m,html:I,lheading:$,list:_,newline:/^(?: *(?:\n|$))+/,paragraph:E,table:f,text:/^[^\n]+/},Z=k("^ *([^\\n ].*)\\n {0,3}((?:\\| *)?:?-+:? *(?:\\| *:?-+:? *)*(?:\\| *)?)(?:\\n((?:(?! *\\n|hr|heading|blockquote|code|fences|list|html).*(?:\\n|$))*)\\n*|$)").replace("hr",m).replace("heading"," {0,3}#{1,6}(?:\\s|$)").replace("blockquote"," {0,3}>").replace("code"," {4}[^\\n]").replace("fences"," {0,3}(?:`{3,}(?=[^`\\n]*\\n)|~{3,})[^\\n]*\\n").replace("list"," {0,3}(?:[*+-]|1[.)]) ").replace("html","</?(?:tag)(?: +|\\n|/?>)|<(?:script|pre|style|textarea|!--)").replace("tag",A).getRegex(),L={...q,table:Z,paragraph:k(z).replace("hr",m).replace("heading"," {0,3}#{1,6}(?:\\s|$)").replace("|lheading","").replace("table",Z).replace("blockquote"," {0,3}>").replace("fences"," {0,3}(?:`{3,}(?=[^`\\n]*\\n)|~{3,})[^\\n]*\\n").replace("list"," {0,3}(?:[*+-]|1[.)]) ").replace("html","</?(?:tag)(?: +|\\n|/?>)|<(?:script|pre|style|textarea|!--)").replace("tag",A).getRegex()},P={...q,html:k("^ *(?:comment *(?:\\n|\\s*$)|<(tag)[\\s\\S]+?</\\1> *(?:\\n{2,}|\\s*$)|<tag(?:\"[^\"]*\"|'[^']*'|\\s[^'\"/>\\s]*)*?/?> *(?:\\n{2,}|\\s*$))").replace("comment",S).replace(/tag/g,"(?!(?:a|em|strong|small|s|cite|q|dfn|abbr|data|time|code|var|samp|kbd|sub|sup|i|b|u|mark|ruby|rt|rp|bdi|bdo|span|br|wbr|ins|del|img)\\b)\\w+(?!:|[^\\w\\s@]*@)\\b").getRegex(),def:/^ *\[([^\]]+)\]: *<?([^\s>]+)>?(?: +(["(][^\n]+[")]))? *(?:\n+|$)/,heading:/^(#{1,6})(.*)(?:\n+|$)/,fences:f,lheading:/^(.+?)\n {0,3}(=+|-+) *(?:\n+|$)/,paragraph:k(z).replace("hr",m).replace("heading"," *#{1,6} *[^\n]").replace("lheading",$).replace("|table","").replace("blockquote"," {0,3}>").replace("|fences","").replace("|list","").replace("|html","").replace("|tag","").getRegex()},Q=/^\\([!"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])/,v=/^( {2,}|\\)\n(?!\s*$)/,B="\\p{P}\\p{S}",C=k(/^((?![*_])[\spunctuation])/,"u").replace(/punctuation/g,B).getRegex(),M=k(/^(?:\*+(?:((?!\*)[punct])|[^\s*]))|^_+(?:((?!_)[punct])|([^\s_]))/,"u").replace(/punct/g,B).getRegex(),O=k("^[^_*]*?__[^_*]*?\\*[^_*]*?(?=__)|[^*]+(?=[^*])|(?!\\*)[punct](\\*+)(?=[\\s]|$)|[^punct\\s](\\*+)(?!\\*)(?=[punct\\s]|$)|(?!\\*)[punct\\s](\\*+)(?=[^punct\\s])|[\\s](\\*+)(?!\\*)(?=[punct])|(?!\\*)[punct](\\*+)(?!\\*)(?=[punct])|[^punct\\s](\\*+)(?=[^punct\\s])","gu").replace(/punct/g,B).getRegex(),D=k("^[^_*]*?\\*\\*[^_*]*?_[^_*]*?(?=\\*\\*)|[^_]+(?=[^_])|(?!_)[punct](_+)(?=[\\s]|$)|[^punct\\s](_+)(?!_)(?=[punct\\s]|$)|(?!_)[punct\\s](_+)(?=[^punct\\s])|[\\s](_+)(?!_)(?=[punct])|(?!_)[punct](_+)(?!_)(?=[punct])","gu").replace(/punct/g,B).getRegex(),j=k(/\\([punct])/,"gu").replace(/punct/g,B).getRegex(),H=k(/^<(scheme:[^\s\x00-\x1f<>]*|email)>/).replace("scheme",/[a-zA-Z][a-zA-Z0-9+.-]{1,31}/).replace("email",/[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+(@)[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+(?![-_])/).getRegex(),U=k(S).replace("(?:--\x3e|$)","--\x3e").getRegex(),X=k("^comment|^</[a-zA-Z][\\w:-]*\\s*>|^<[a-zA-Z][\\w-]*(?:attribute)*?\\s*/?>|^<\\?[\\s\\S]*?\\?>|^<![a-zA-Z]+\\s[\\s\\S]*?>|^<!\\[CDATA\\[[\\s\\S]*?\\]\\]>").replace("comment",U).replace("attribute",/\s+[a-zA-Z:_][\w.:-]*(?:\s*=\s*"[^"]*"|\s*=\s*'[^']*'|\s*=\s*[^\s"'=<>`]+)?/).getRegex(),F=/(?:\[(?:\\.|[^\[\]\\])*\]|\\.|`[^`]*`|[^\[\]\\`])*?/,N=k(/^!?\[(label)\]\(\s*(href)(?:\s+(title))?\s*\)/).replace("label",F).replace("href",/<(?:\\.|[^\n<>\\])+>|[^\s\x00-\x1f]*/).replace("title",/"(?:\\"?|[^"\\])*"|'(?:\\'?|[^'\\])*'|\((?:\\\)?|[^)\\])*\)/).getRegex(),G=k(/^!?\[(label)\]\[(ref)\]/).replace("label",F).replace("ref",T).getRegex(),J=k(/^!?\[(ref)\](?:\[\])?/).replace("ref",T).getRegex(),K={_backpedal:f,anyPunctuation:j,autolink:H,blockSkip:/\[[^[\]]*?\]\([^\(\)]*?\)|`[^`]*?`|<[^<>]*?>/g,br:v,code:/^(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/,del:f,emStrongLDelim:M,emStrongRDelimAst:O,emStrongRDelimUnd:D,escape:Q,link:N,nolink:J,punctuation:C,reflink:G,reflinkSearch:k("reflink|nolink(?!\\()","g").replace("reflink",G).replace("nolink",J).getRegex(),tag:X,text:/^(`+|[^`])(?:(?= {2,}\n)|[\s\S]*?(?:(?=[\\<!\[`*_]|\b_|$)|[^ ](?= {2,}\n)))/,url:f},V={...K,link:k(/^!?\[(label)\]\((.*?)\)/).replace("label",F).getRegex(),reflink:k(/^!?\[(label)\]\s*\[([^\]]*)\]/).replace("label",F).getRegex()},W={...K,escape:k(Q).replace("])","~|])").getRegex(),url:k(/^((?:ftp|https?):\/\/|www\.)(?:[a-zA-Z0-9\-]+\.?)+[^\s<]*|^email/,"i").replace("email",/[A-Za-z0-9._+-]+(@)[a-zA-Z0-9-_]+(?:\.[a-zA-Z0-9-_]*[a-zA-Z0-9])+(?![-_])/).getRegex(),_backpedal:/(?:[^?!.,:;*_'"~()&]+|\([^)]*\)|&(?![a-zA-Z0-9]+;$)|[?!.,:;*_'"~)]+(?!$))+/,del:/^(~~?)(?=[^\s~])([\s\S]*?[^\s~])\1(?=[^~]|$)/,text:/^([`~]+|[^`~])(?:(?= {2,}\n)|(?=[a-zA-Z0-9.!#$%&'*+\/=?_`{\|}~-]+@)|[\s\S]*?(?:(?=[\\<!\[`*~_]|\b_|https?:\/\/|ftp:\/\/|www\.|$)|[^ ](?= {2,}\n)|[^a-zA-Z0-9.!#$%&'*+\/=?_`{\|}~-](?=[a-zA-Z0-9.!#$%&'*+\/=?_`{\|}~-]+@)))/},Y={...W,br:k(v).replace("{2,}","*").getRegex(),text:k(W.text).replace("\\b_","\\b_| {2,}\\n").replace(/\{2,\}/g,"*").getRegex()},ee={normal:q,gfm:L,pedantic:P},te={normal:K,gfm:W,breaks:Y,pedantic:V};class ne{tokens;options;state;tokenizer;inlineQueue;constructor(t){this.tokens=[],this.tokens.links=Object.create(null),this.options=t||e.defaults,this.options.tokenizer=this.options.tokenizer||new w,this.tokenizer=this.options.tokenizer,this.tokenizer.options=this.options,this.tokenizer.lexer=this,this.inlineQueue=[],this.state={inLink:!1,inRawBlock:!1,top:!0};const n={block:ee.normal,inline:te.normal};this.options.pedantic?(n.block=ee.pedantic,n.inline=te.pedantic):this.options.gfm&&(n.block=ee.gfm,this.options.breaks?n.inline=te.breaks:n.inline=te.gfm),this.tokenizer.rules=n}static get rules(){return{block:ee,inline:te}}static lex(e,t){return new ne(t).lex(e)}static lexInline(e,t){return new ne(t).inlineTokens(e)}lex(e){e=e.replace(/\r\n|\r/g,"\n"),this.blockTokens(e,this.tokens);for(let e=0;e<this.inlineQueue.length;e++){const t=this.inlineQueue[e];this.inlineTokens(t.src,t.tokens)}return this.inlineQueue=[],this.tokens}blockTokens(e,t=[]){let n,s,r,i;for(e=this.options.pedantic?e.replace(/\t/g,"    ").replace(/^ +$/gm,""):e.replace(/^( *)(\t+)/gm,((e,t,n)=>t+"    ".repeat(n.length)));e;)if(!(this.options.extensions&&this.options.extensions.block&&this.options.extensions.block.some((s=>!!(n=s.call({lexer:this},e,t))&&(e=e.substring(n.raw.length),t.push(n),!0)))))if(n=this.tokenizer.space(e))e=e.substring(n.raw.length),1===n.raw.length&&t.length>0?t[t.length-1].raw+="\n":t.push(n);else if(n=this.tokenizer.code(e))e=e.substring(n.raw.length),s=t[t.length-1],!s||"paragraph"!==s.type&&"text"!==s.type?t.push(n):(s.raw+="\n"+n.raw,s.text+="\n"+n.text,this.inlineQueue[this.inlineQueue.length-1].src=s.text);else if(n=this.tokenizer.fences(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.heading(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.hr(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.blockquote(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.list(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.html(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.def(e))e=e.substring(n.raw.length),s=t[t.length-1],!s||"paragraph"!==s.type&&"text"!==s.type?this.tokens.links[n.tag]||(this.tokens.links[n.tag]={href:n.href,title:n.title}):(s.raw+="\n"+n.raw,s.text+="\n"+n.raw,this.inlineQueue[this.inlineQueue.length-1].src=s.text);else if(n=this.tokenizer.table(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.lheading(e))e=e.substring(n.raw.length),t.push(n);else{if(r=e,this.options.extensions&&this.options.extensions.startBlock){let t=1/0;const n=e.slice(1);let s;this.options.extensions.startBlock.forEach((e=>{s=e.call({lexer:this},n),"number"==typeof s&&s>=0&&(t=Math.min(t,s))})),t<1/0&&t>=0&&(r=e.substring(0,t+1))}if(this.state.top&&(n=this.tokenizer.paragraph(r)))s=t[t.length-1],i&&"paragraph"===s.type?(s.raw+="\n"+n.raw,s.text+="\n"+n.text,this.inlineQueue.pop(),this.inlineQueue[this.inlineQueue.length-1].src=s.text):t.push(n),i=r.length!==e.length,e=e.substring(n.raw.length);else if(n=this.tokenizer.text(e))e=e.substring(n.raw.length),s=t[t.length-1],s&&"text"===s.type?(s.raw+="\n"+n.raw,s.text+="\n"+n.text,this.inlineQueue.pop(),this.inlineQueue[this.inlineQueue.length-1].src=s.text):t.push(n);else if(e){const t="Infinite loop on byte: "+e.charCodeAt(0);if(this.options.silent){console.error(t);break}throw new Error(t)}}return this.state.top=!0,t}inline(e,t=[]){return this.inlineQueue.push({src:e,tokens:t}),t}inlineTokens(e,t=[]){let n,s,r,i,l,o,a=e;if(this.tokens.links){const e=Object.keys(this.tokens.links);if(e.length>0)for(;null!=(i=this.tokenizer.rules.inline.reflinkSearch.exec(a));)e.includes(i[0].slice(i[0].lastIndexOf("[")+1,-1))&&(a=a.slice(0,i.index)+"["+"a".repeat(i[0].length-2)+"]"+a.slice(this.tokenizer.rules.inline.reflinkSearch.lastIndex))}for(;null!=(i=this.tokenizer.rules.inline.blockSkip.exec(a));)a=a.slice(0,i.index)+"["+"a".repeat(i[0].length-2)+"]"+a.slice(this.tokenizer.rules.inline.blockSkip.lastIndex);for(;null!=(i=this.tokenizer.rules.inline.anyPunctuation.exec(a));)a=a.slice(0,i.index)+"++"+a.slice(this.tokenizer.rules.inline.anyPunctuation.lastIndex);for(;e;)if(l||(o=""),l=!1,!(this.options.extensions&&this.options.extensions.inline&&this.options.extensions.inline.some((s=>!!(n=s.call({lexer:this},e,t))&&(e=e.substring(n.raw.length),t.push(n),!0)))))if(n=this.tokenizer.escape(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.tag(e))e=e.substring(n.raw.length),s=t[t.length-1],s&&"text"===n.type&&"text"===s.type?(s.raw+=n.raw,s.text+=n.text):t.push(n);else if(n=this.tokenizer.link(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.reflink(e,this.tokens.links))e=e.substring(n.raw.length),s=t[t.length-1],s&&"text"===n.type&&"text"===s.type?(s.raw+=n.raw,s.text+=n.text):t.push(n);else if(n=this.tokenizer.emStrong(e,a,o))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.codespan(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.br(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.del(e))e=e.substring(n.raw.length),t.push(n);else if(n=this.tokenizer.autolink(e))e=e.substring(n.raw.length),t.push(n);else if(this.state.inLink||!(n=this.tokenizer.url(e))){if(r=e,this.options.extensions&&this.options.extensions.startInline){let t=1/0;const n=e.slice(1);let s;this.options.extensions.startInline.forEach((e=>{s=e.call({lexer:this},n),"number"==typeof s&&s>=0&&(t=Math.min(t,s))})),t<1/0&&t>=0&&(r=e.substring(0,t+1))}if(n=this.tokenizer.inlineText(r))e=e.substring(n.raw.length),"_"!==n.raw.slice(-1)&&(o=n.raw.slice(-1)),l=!0,s=t[t.length-1],s&&"text"===s.type?(s.raw+=n.raw,s.text+=n.text):t.push(n);else if(e){const t="Infinite loop on byte: "+e.charCodeAt(0);if(this.options.silent){console.error(t);break}throw new Error(t)}}else e=e.substring(n.raw.length),t.push(n);return t}}class se{options;constructor(t){this.options=t||e.defaults}code(e,t,n){const s=(t||"").match(/^\S*/)?.[0];return e=e.replace(/\n$/,"")+"\n",s?'<pre><code class="language-'+c(s)+'">'+(n?e:c(e,!0))+"</code></pre>\n":"<pre><code>"+(n?e:c(e,!0))+"</code></pre>\n"}blockquote(e){return`<blockquote>\n${e}</blockquote>\n`}html(e,t){return e}heading(e,t,n){return`<h${t}>${e}</h${t}>\n`}hr(){return"<hr>\n"}list(e,t,n){const s=t?"ol":"ul";return"<"+s+(t&&1!==n?' start="'+n+'"':"")+">\n"+e+"</"+s+">\n"}listitem(e,t,n){return`<li>${e}</li>\n`}checkbox(e){return"<input "+(e?'checked="" ':"")+'disabled="" type="checkbox">'}paragraph(e){return`<p>${e}</p>\n`}table(e,t){return t&&(t=`<tbody>${t}</tbody>`),"<table>\n<thead>\n"+e+"</thead>\n"+t+"</table>\n"}tablerow(e){return`<tr>\n${e}</tr>\n`}tablecell(e,t){const n=t.header?"th":"td";return(t.align?`<${n} align="${t.align}">`:`<${n}>`)+e+`</${n}>\n`}strong(e){return`<strong>${e}</strong>`}em(e){return`<em>${e}</em>`}codespan(e){return`<code>${e}</code>`}br(){return"<br>"}del(e){return`<del>${e}</del>`}link(e,t,n){const s=g(e);if(null===s)return n;let r='<a href="'+(e=s)+'"';return t&&(r+=' title="'+t+'"'),r+=">"+n+"</a>",r}image(e,t,n){const s=g(e);if(null===s)return n;let r=`<img src="${e=s}" alt="${n}"`;return t&&(r+=` title="${t}"`),r+=">",r}text(e){return e}}class re{strong(e){return e}em(e){return e}codespan(e){return e}del(e){return e}html(e){return e}text(e){return e}link(e,t,n){return""+n}image(e,t,n){return""+n}br(){return""}}class ie{options;renderer;textRenderer;constructor(t){this.options=t||e.defaults,this.options.renderer=this.options.renderer||new se,this.renderer=this.options.renderer,this.renderer.options=this.options,this.textRenderer=new re}static parse(e,t){return new ie(t).parse(e)}static parseInline(e,t){return new ie(t).parseInline(e)}parse(e,t=!0){let n="";for(let s=0;s<e.length;s++){const r=e[s];if(this.options.extensions&&this.options.extensions.renderers&&this.options.extensions.renderers[r.type]){const e=r,t=this.options.extensions.renderers[e.type].call({parser:this},e);if(!1!==t||!["space","hr","heading","code","table","blockquote","list","html","paragraph","text"].includes(e.type)){n+=t||"";continue}}switch(r.type){case"space":continue;case"hr":n+=this.renderer.hr();continue;case"heading":{const e=r;n+=this.renderer.heading(this.parseInline(e.tokens),e.depth,p(this.parseInline(e.tokens,this.textRenderer)));continue}case"code":{const e=r;n+=this.renderer.code(e.text,e.lang,!!e.escaped);continue}case"table":{const e=r;let t="",s="";for(let t=0;t<e.header.length;t++)s+=this.renderer.tablecell(this.parseInline(e.header[t].tokens),{header:!0,align:e.align[t]});t+=this.renderer.tablerow(s);let i="";for(let t=0;t<e.rows.length;t++){const n=e.rows[t];s="";for(let t=0;t<n.length;t++)s+=this.renderer.tablecell(this.parseInline(n[t].tokens),{header:!1,align:e.align[t]});i+=this.renderer.tablerow(s)}n+=this.renderer.table(t,i);continue}case"blockquote":{const e=r,t=this.parse(e.tokens);n+=this.renderer.blockquote(t);continue}case"list":{const e=r,t=e.ordered,s=e.start,i=e.loose;let l="";for(let t=0;t<e.items.length;t++){const n=e.items[t],s=n.checked,r=n.task;let o="";if(n.task){const e=this.renderer.checkbox(!!s);i?n.tokens.length>0&&"paragraph"===n.tokens[0].type?(n.tokens[0].text=e+" "+n.tokens[0].text,n.tokens[0].tokens&&n.tokens[0].tokens.length>0&&"text"===n.tokens[0].tokens[0].type&&(n.tokens[0].tokens[0].text=e+" "+n.tokens[0].tokens[0].text)):n.tokens.unshift({type:"text",text:e+" "}):o+=e+" "}o+=this.parse(n.tokens,i),l+=this.renderer.listitem(o,r,!!s)}n+=this.renderer.list(l,t,s);continue}case"html":{const e=r;n+=this.renderer.html(e.text,e.block);continue}case"paragraph":{const e=r;n+=this.renderer.paragraph(this.parseInline(e.tokens));continue}case"text":{let i=r,l=i.tokens?this.parseInline(i.tokens):i.text;for(;s+1<e.length&&"text"===e[s+1].type;)i=e[++s],l+="\n"+(i.tokens?this.parseInline(i.tokens):i.text);n+=t?this.renderer.paragraph(l):l;continue}default:{const e='Token with "'+r.type+'" type was not found.';if(this.options.silent)return console.error(e),"";throw new Error(e)}}}return n}parseInline(e,t){t=t||this.renderer;let n="";for(let s=0;s<e.length;s++){const r=e[s];if(this.options.extensions&&this.options.extensions.renderers&&this.options.extensions.renderers[r.type]){const e=this.options.extensions.renderers[r.type].call({parser:this},r);if(!1!==e||!["escape","html","link","image","strong","em","codespan","br","del","text"].includes(r.type)){n+=e||"";continue}}switch(r.type){case"escape":{const e=r;n+=t.text(e.text);break}case"html":{const e=r;n+=t.html(e.text);break}case"link":{const e=r;n+=t.link(e.href,e.title,this.parseInline(e.tokens,t));break}case"image":{const e=r;n+=t.image(e.href,e.title,e.text);break}case"strong":{const e=r;n+=t.strong(this.parseInline(e.tokens,t));break}case"em":{const e=r;n+=t.em(this.parseInline(e.tokens,t));break}case"codespan":{const e=r;n+=t.codespan(e.text);break}case"br":n+=t.br();break;case"del":{const e=r;n+=t.del(this.parseInline(e.tokens,t));break}case"text":{const e=r;n+=t.text(e.text);break}default:{const e='Token with "'+r.type+'" type was not found.';if(this.options.silent)return console.error(e),"";throw new Error(e)}}}return n}}class le{options;constructor(t){this.options=t||e.defaults}static passThroughHooks=new Set(["preprocess","postprocess","processAllTokens"]);preprocess(e){return e}postprocess(e){return e}processAllTokens(e){return e}}class oe{defaults={async:!1,breaks:!1,extensions:null,gfm:!0,hooks:null,pedantic:!1,renderer:null,silent:!1,tokenizer:null,walkTokens:null};options=this.setOptions;parse=this.#e(ne.lex,ie.parse);parseInline=this.#e(ne.lexInline,ie.parseInline);Parser=ie;Renderer=se;TextRenderer=re;Lexer=ne;Tokenizer=w;Hooks=le;constructor(...e){this.use(...e)}walkTokens(e,t){let n=[];for(const s of e)switch(n=n.concat(t.call(this,s)),s.type){case"table":{const e=s;for(const s of e.header)n=n.concat(this.walkTokens(s.tokens,t));for(const s of e.rows)for(const e of s)n=n.concat(this.walkTokens(e.tokens,t));break}case"list":{const e=s;n=n.concat(this.walkTokens(e.items,t));break}default:{const e=s;this.defaults.extensions?.childTokens?.[e.type]?this.defaults.extensions.childTokens[e.type].forEach((s=>{const r=e[s].flat(1/0);n=n.concat(this.walkTokens(r,t))})):e.tokens&&(n=n.concat(this.walkTokens(e.tokens,t)))}}return n}use(...e){const t=this.defaults.extensions||{renderers:{},childTokens:{}};return e.forEach((e=>{const n={...e};if(n.async=this.defaults.async||n.async||!1,e.extensions&&(e.extensions.forEach((e=>{if(!e.name)throw new Error("extension name required");if("renderer"in e){const n=t.renderers[e.name];t.renderers[e.name]=n?function(...t){let s=e.renderer.apply(this,t);return!1===s&&(s=n.apply(this,t)),s}:e.renderer}if("tokenizer"in e){if(!e.level||"block"!==e.level&&"inline"!==e.level)throw new Error("extension level must be 'block' or 'inline'");const n=t[e.level];n?n.unshift(e.tokenizer):t[e.level]=[e.tokenizer],e.start&&("block"===e.level?t.startBlock?t.startBlock.push(e.start):t.startBlock=[e.start]:"inline"===e.level&&(t.startInline?t.startInline.push(e.start):t.startInline=[e.start]))}"childTokens"in e&&e.childTokens&&(t.childTokens[e.name]=e.childTokens)})),n.extensions=t),e.renderer){const t=this.defaults.renderer||new se(this.defaults);for(const n in e.renderer){if(!(n in t))throw new Error(`renderer '${n}' does not exist`);if("options"===n)continue;const s=n,r=e.renderer[s],i=t[s];t[s]=(...e)=>{let n=r.apply(t,e);return!1===n&&(n=i.apply(t,e)),n||""}}n.renderer=t}if(e.tokenizer){const t=this.defaults.tokenizer||new w(this.defaults);for(const n in e.tokenizer){if(!(n in t))throw new Error(`tokenizer '${n}' does not exist`);if(["options","rules","lexer"].includes(n))continue;const s=n,r=e.tokenizer[s],i=t[s];t[s]=(...e)=>{let n=r.apply(t,e);return!1===n&&(n=i.apply(t,e)),n}}n.tokenizer=t}if(e.hooks){const t=this.defaults.hooks||new le;for(const n in e.hooks){if(!(n in t))throw new Error(`hook '${n}' does not exist`);if("options"===n)continue;const s=n,r=e.hooks[s],i=t[s];le.passThroughHooks.has(n)?t[s]=e=>{if(this.defaults.async)return Promise.resolve(r.call(t,e)).then((e=>i.call(t,e)));const n=r.call(t,e);return i.call(t,n)}:t[s]=(...e)=>{let n=r.apply(t,e);return!1===n&&(n=i.apply(t,e)),n}}n.hooks=t}if(e.walkTokens){const t=this.defaults.walkTokens,s=e.walkTokens;n.walkTokens=function(e){let n=[];return n.push(s.call(this,e)),t&&(n=n.concat(t.call(this,e))),n}}this.defaults={...this.defaults,...n}})),this}setOptions(e){return this.defaults={...this.defaults,...e},this}lexer(e,t){return ne.lex(e,t??this.defaults)}parser(e,t){return ie.parse(e,t??this.defaults)}#e(e,t){return(n,s)=>{const r={...s},i={...this.defaults,...r};!0===this.defaults.async&&!1===r.async&&(i.silent||console.warn("marked(): The async option was set to true by an extension. The async: false option sent to parse will be ignored."),i.async=!0);const l=this.#t(!!i.silent,!!i.async);if(null==n)return l(new Error("marked(): input parameter is undefined or null"));if("string"!=typeof n)return l(new Error("marked(): input parameter is of type "+Object.prototype.toString.call(n)+", string expected"));if(i.hooks&&(i.hooks.options=i),i.async)return Promise.resolve(i.hooks?i.hooks.preprocess(n):n).then((t=>e(t,i))).then((e=>i.hooks?i.hooks.processAllTokens(e):e)).then((e=>i.walkTokens?Promise.all(this.walkTokens(e,i.walkTokens)).then((()=>e)):e)).then((e=>t(e,i))).then((e=>i.hooks?i.hooks.postprocess(e):e)).catch(l);try{i.hooks&&(n=i.hooks.preprocess(n));let s=e(n,i);i.hooks&&(s=i.hooks.processAllTokens(s)),i.walkTokens&&this.walkTokens(s,i.walkTokens);let r=t(s,i);return i.hooks&&(r=i.hooks.postprocess(r)),r}catch(e){return l(e)}}}#t(e,t){return n=>{if(n.message+="\nPlease report this to https://github.com/markedjs/marked.",e){const e="<p>An error occurred:</p><pre>"+c(n.message+"",!0)+"</pre>";return t?Promise.resolve(e):e}if(t)return Promise.reject(n);throw n}}}const ae=new oe;function ce(e,t){return ae.parse(e,t)}ce.options=ce.setOptions=function(e){return ae.setOptions(e),ce.defaults=ae.defaults,n(ce.defaults),ce},ce.getDefaults=t,ce.defaults=e.defaults,ce.use=function(...e){return ae.use(...e),ce.defaults=ae.defaults,n(ce.defaults),ce},ce.walkTokens=function(e,t){return ae.walkTokens(e,t)},ce.parseInline=ae.parseInline,ce.Parser=ie,ce.parser=ie.parse,ce.Renderer=se,ce.TextRenderer=re,ce.Lexer=ne,ce.lexer=ne.lex,ce.Tokenizer=w,ce.Hooks=le,ce.parse=ce;const he=ce.options,pe=ce.setOptions,ue=ce.use,ke=ce.walkTokens,ge=ce.parseInline,fe=ce,de=ie.parse,xe=ne.lex;e.Hooks=le,e.Lexer=ne,e.Marked=oe,e.Parser=ie,e.Renderer=se,e.TextRenderer=re,e.Tokenizer=w,e.getDefaults=t,e.lexer=xe,e.marked=ce,e.options=he,e.parse=fe,e.parseInline=ge,e.parser=de,e.setOptions=pe,e.use=ue,e.walkTokens=ke}));

</script>
<style>
:root {
  --bg: #000000; --bg2: rgba(255,255,255,0.04); --bg3: rgba(255,255,255,0.08);
  --fg: #F5F5F7; --fg2: rgba(245,245,247,0.55); --fg3: rgba(245,245,247,0.3);
  --primary: #2997FF; --error: #FF453A; --success: #30D158; --warn: #FFD60A;
  --border: rgba(255,255,255,0.1);
  --user-bg: rgba(41,151,255,0.12); --user-accent: #2997FF;
  --assistant-bg: rgba(255,255,255,0.05); --assistant-accent: rgba(255,255,255,0.15);
  --tool-bg: rgba(48,209,88,0.08); --tool-accent: #30D158;
  --radius: 14px;
}

/* ---- Neumorphism (Light) ---- */
[data-theme="neumorphism"] {
  --bg: #E0E5EC; --bg2: #E0E5EC; --bg3: #D1D9E6;
  --fg: #3D4852; --fg2: #6B7B8D; --fg3: #9AABB8;
  --primary: #6C63FF; --error: #FC5C65; --success: #26DE81; --warn: #F7B731;
  --border: #D1D9E6;
  --user-bg: #E0E5EC; --user-accent: #6C63FF;
  --assistant-bg: #E0E5EC; --assistant-accent: #A0AEC0;
  --tool-bg: #E0E5EC; --tool-accent: #38B2AC;
  --radius: 16px;
}
/* ---- Aurora Mesh (Dark) ---- */
[data-theme="aurora"] {
  --bg: #09090B; --bg2: #18181B; --bg3: #27272A;
  --fg: #FAFAFA; --fg2: #A1A1AA; --fg3: #71717A;
  --primary: #8B5CF6; --error: #FB7185; --success: #34D399; --warn: #FBBF24;
  --border: rgba(255,255,255,0.06);
  --user-bg: rgba(139,92,246,0.1); --user-accent: #8B5CF6;
  --assistant-bg: rgba(255,255,255,0.03); --assistant-accent: #3F3F46;
  --tool-bg: rgba(45,212,191,0.06); --tool-accent: #2DD4BF;
  --radius: 12px;
}
/* ---- Enterprise (Light) ---- */
[data-theme="enterprise"] {
  --bg: #F8FAFC; --bg2: #F1F5F9; --bg3: #E2E8F0;
  --fg: #1E293B; --fg2: #64748B; --fg3: #94A3B8;
  --primary: #2563EB; --error: #DC2626; --success: #16A34A; --warn: #D97706;
  --border: #E2E8F0;
  --user-bg: #DBEAFE; --user-accent: #2563EB;
  --assistant-bg: #F8FAFC; --assistant-accent: #94A3B8;
  --tool-bg: #F0FDF4; --tool-accent: #16A34A;
  --radius: 8px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, 'Inter', system-ui, sans-serif; background: var(--bg); color: var(--fg); height: 100vh; display: flex; flex-direction: column; }
.header { display: flex; align-items: center; gap: 12px; padding: 10px 18px; background: var(--bg2); backdrop-filter: blur(40px); border-bottom: 1px solid var(--border); z-index: 10; }
.header h1 { font-size: 15px; font-weight: 600; }
.header select { background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; font-size: 12px; }
.header .db-info { font-size: 11px; color: var(--fg2); }
.main { display: flex; flex: 1; overflow: hidden; }
.sidebar { width: 280px; background: var(--bg2); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.search-box { padding: 10px; }
.search-box input { width: 100%; padding: 8px 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--fg); font-size: 13px; outline: none; }
.session-list { flex: 1; overflow-y: auto; padding: 0 8px 12px; }
.session-item { padding: 8px 10px; border-radius: 10px; cursor: pointer; margin-bottom: 3px; }
.session-item:hover { background: var(--bg3); }
.session-item.active { background: rgba(41,151,255,0.15); }
.sess-title { font-size: 13px; line-height: 1.35; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-meta { font-size: 10px; color: var(--fg3); margin-top: 3px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.sess-badge { padding: 1px 5px; border-radius: 4px; font-size: 10px; }
.sess-badge-tools { background: rgba(48,209,88,0.18); color: var(--tool-accent); }
.sess-badge-token { background: rgba(41,151,255,0.15); color: var(--primary); }
.sess-badge-sub { background: rgba(255,214,10,0.15); color: var(--warn); }
.content { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.conv-header { padding: 12px 20px; background: var(--bg2); border-bottom: 1px solid var(--border); }
.conv-title { font-size: 14px; font-weight: 600; }
.conv-stats { font-size: 11px; color: var(--fg2); margin-top: 4px; }
.messages { flex: 1; overflow-y: auto; padding: 20px; }
.msg { display: flex; gap: 10px; margin-bottom: 14px; }
.msg-avatar { width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; }
.msg-user .msg-avatar { background: rgba(41,151,255,0.25); color: var(--primary); }
.msg-assistant .msg-avatar { background: var(--bg3); color: var(--fg2); }
.msg-body { flex: 1; min-width: 0; }
.msg-role { font-size: 10px; color: var(--fg3); margin-bottom: 3px; }
.msg-inner { padding: 10px 14px; border-radius: var(--radius); font-size: 13px; line-height: 1.6; overflow-wrap: break-word; }
.msg-user .msg-inner { background: var(--user-bg); border-left: 3px solid var(--user-accent); }
.msg-assistant .msg-inner { background: var(--assistant-bg); border-left: 3px solid var(--assistant-accent); }
.tool-call { background: var(--tool-bg); border-left: 3px solid var(--tool-accent); border-radius: var(--radius); padding: 8px 14px; margin-bottom: 8px; }
.tool-row { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 13px; }
.tool-name { color: var(--tool-accent); font-weight: 600; }
.tool-status { font-size: 10px; padding: 1px 6px; border-radius: 4px; background: var(--bg3); color: var(--fg2); }
.tool-status.error { background: rgba(255,69,58,0.2); color: var(--error); }
.tool-summary { color: var(--fg2); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.tool-detail { display: none; margin-top: 8px; }
.tool-call.open .tool-detail { display: block; }
.tool-label { font-size: 10px; color: var(--fg3); margin: 6px 0 3px; }
pre { white-space: pre-wrap; word-break: break-all; font-size: 12px; background: rgba(0,0,0,0.55); padding: 8px; border-radius: 8px; font-family: 'SF Mono', ui-monospace, monospace; }
.reasoning-block { background: var(--bg2); border-left: 3px solid var(--border); border-radius: var(--radius); padding: 6px 12px; }
.reasoning-toggle { cursor: pointer; font-size: 11px; color: var(--fg3); }
.reasoning-content { display: none; margin-top: 6px; font-size: 12px; color: var(--fg2); }
.reasoning-content.open { display: block; }
.retrieval-summary {
  padding: 4px 12px; margin: 4px 0; font-size: 11px; cursor: pointer;
  background: var(--bg2); border-radius: 6px; color: var(--fg3);
  border-left: 2px solid var(--border); display: inline-block;
}
.retrieval-summary:hover { background: var(--bg3); }
.usage-marker { font-size: 11px; color: var(--primary); padding: 2px 8px; margin: 6px 0; background: rgba(41,151,255,0.08); border-radius: 6px; display: inline-block; }
.stats-bar { padding: 10px 20px; background: var(--bg2); border-top: 1px solid var(--border); display: flex; gap: 20px; font-size: 12px; color: var(--fg2); flex-wrap: wrap; }
.stats-bar b { color: var(--success); font-weight: 600; }
.empty-state { color: var(--fg3); text-align: center; padding: 60px 20px; font-size: 13px; }
.md-content p { margin: 4px 0; } .md-content code { background: rgba(0,0,0,0.4); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.md-content pre code { background: none; padding: 0; }
.md-content table { border-collapse: collapse; margin: 6px 0; } .md-content th, .md-content td { border: 1px solid var(--border); padding: 4px 8px; font-size: 12px; }
.tree-line { color: var(--fg3); }
</style>
</head>
<body>
<div class="header">
  <h1>Session Trace Viewer</h1>
  <select id="sourceSel" onchange="switchSource(this.value)"></select>
  <span class="db-info" id="dbInfo">connecting…</span>
  <span style="flex:1"></span>
  <div class="theme-switcher">
    <button class="theme-dot" style="background:#2997FF" onclick="setTheme('glassmorphism')" title="Glassmorphism (Dark)"></button>
    <button class="theme-dot" style="background:#E0E5EC" onclick="setTheme('neumorphism')" title="Neumorphism (Light)"></button>
    <button class="theme-dot" style="background:#8B5CF6" onclick="setTheme('aurora')" title="Aurora Mesh (Dark)"></button>
    <button class="theme-dot" style="background:#2563EB" onclick="setTheme('enterprise')" title="Enterprise (Light)"></button>
  </div>
  <style>
  .theme-dot { width: 18px; height: 18px; border-radius: 50%; border: 2px solid transparent; cursor: pointer; }
  .theme-dot.active { border-color: #fff; box-shadow: 0 0 4px rgba(255,255,255,0.5); }
  </style>
</div>
<div class="main">
  <div class="sidebar">
    <div class="search-box"><input id="searchInput" placeholder="搜索会话（标题 / id）…" oninput="filterSessions()"></div>
    <div class="session-list" id="sessionList"><div class="empty-state">加载中…</div></div>
  </div>
  <div class="content">
    <div class="conv-header" id="convHeader" style="display:none">
      <div class="conv-title" id="convTitle"></div>
      <div class="conv-stats" id="convStats"></div>
    </div>
    <div class="messages" id="messages"><div class="empty-state">选择左侧会话查看对话过程</div></div>
    <div class="stats-bar" id="statsBar" style="display:none"></div>
  </div>
</div>
<script>
let allSessions = [];
let currentSource = null;

// CDN 脚本（marked/hljs）加载失败不致命：无 marked 时降级为转义文本
if (window.marked && marked.setOptions) { try { marked.setOptions({ breaks: true, gfm: true }); } catch {} }
function esc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }
function renderMd(t) {
  if (!t) return '';
  if (window.marked && marked.parse) { try { return marked.parse(t); } catch { /* 降级 */ } }
  return '<div style="white-space:pre-wrap">' + esc(t) + '</div>';
}
function fmt(n) { return n == null ? '-' : Number(n).toLocaleString(); }
function fmtTok(n) { if (n == null) return '-'; if (n >= 1e6) return (n/1e6).toFixed(1)+'M'; if (n >= 1e3) return (n/1e3).toFixed(1)+'K'; return String(n); }

async function boot() {
  let r, d;
  try {
    r = await fetch('/api/sources');
    d = await r.json();
  } catch (e) {
    document.getElementById('dbInfo').textContent =
      '连接失败（' + e.message + '），3 秒后重试…';
    setTimeout(boot, 3000);
    return;
  }
  const sel = document.getElementById('sourceSel');
  sel.innerHTML = d.sources.map(s => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
  if (d.sources.length) switchSource(d.sources[0].id);
}

async function switchSource(id) {
  currentSource = id;
  document.getElementById('sessionList').innerHTML = '<div class="empty-state">加载中…</div>';
  const r = await fetch('/api/sessions?source=' + encodeURIComponent(id));
  const d = await r.json();
  document.getElementById('dbInfo').textContent =
    `${d.count} sessions · ${d.total_messages} messages`;
  allSessions = d.sessions;
  renderSessionList('');
}

function renderSessionList(q) {
  const list = document.getElementById('sessionList');
  list.innerHTML = '';
  const byParent = {};
  allSessions.forEach(s => { (byParent[s.parent_id] = byParent[s.parent_id] || []).push(s); });
  const roots = allSessions.filter(s => !s.parent_id || !allSessions.find(x => x.id === s.parent_id));
  let n = 0;
  const render = (s, depth) => {
    if (q && !(s.title.toLowerCase().includes(q) || s.id.includes(q))) { (byParent[s.id]||[]).forEach(c => render(c, depth)); return; }
    n++;
    const el = document.createElement('div');
    el.className = 'session-item'; el.dataset.id = s.id;
    const badges = [];
    if (s.tool_count) badges.push(`<span class="sess-badge sess-badge-tools">${s.tool_count}T</span>`);
    if (s.input_tokens) badges.push(`<span class="sess-badge sess-badge-token">↑${fmtTok(s.input_tokens)}</span>`);
    if (s.kind && s.kind !== 'root') badges.push(`<span class="sess-badge sess-badge-sub">${esc(s.kind)}</span>`);
    const time = (s.updated_at || s.created_at) ? new Date((s.updated_at || s.created_at) > 1e12 ? (s.updated_at || s.created_at) : (s.updated_at || s.created_at)*1000).toLocaleString('zh-CN') : '';
    const indent = depth ? '<span class="tree-line">' + '\u00A0\u00A0'.repeat(depth) + '└</span> ' : '';
    el.innerHTML = `<div class="sess-title">${indent}${esc((s.title||'').slice(0,60))}</div>
      <div class="sess-meta"><span>${esc(s.id.slice(0,10))}…</span>${badges.join('')}<span>${s.n_msgs}msg</span><span>${time}</span></div>`;
    el.onclick = () => selectSession(s.id, el);
    el.oncontextmenu = (e) => { e.preventDefault(); navigator.clipboard.writeText(s.id); };
    list.appendChild(el);
    (byParent[s.id]||[]).forEach(c => render(c, depth+1));
  };
  roots.forEach(r => render(r, 0));
}

function filterSessions() { renderSessionList(document.getElementById('searchInput').value.toLowerCase()); }

async function selectSession(id, el) {
  document.querySelectorAll('.session-item').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  const r = await fetch('/api/messages?source=' + encodeURIComponent(currentSource) + '&session=' + encodeURIComponent(id));
  const d = await r.json();
  if (d.error) { document.getElementById('messages').innerHTML = `<div class="empty-state">${esc(d.error)}</div>`; return; }
  renderConversation(d);
}

function renderConversation(d) {
  document.getElementById('convHeader').style.display = '';
  document.getElementById('convTitle').textContent = d.title;
  document.getElementById('convTitle').title = d.session_id + '（右键侧栏项可复制 id）';
  document.getElementById('convStats').textContent =
    `${d.messages.length} messages · ${d.stats.tool_count} tools · ${d.stats.reasoning_count} reasoning`;
  const box = document.getElementById('messages');
  box.innerHTML = '';
  for (const m of d.messages) {
    for (const item of m.items) {
      if (item.kind === 'text') {
        const div = document.createElement('div');
        div.className = 'msg msg-' + (m.role === 'user' ? 'user' : 'assistant');
        div.innerHTML = `<div class="msg-avatar">${m.role === 'user' ? 'U' : 'A'}</div>
          <div class="msg-body"><div class="msg-role">${m.role}${m.origin ? ' · ' + m.origin : ''}</div>
          <div class="msg-inner"><div class="md-content">${renderMd(item.text)}</div></div></div>`;
        box.appendChild(div);
      } else if (item.kind === 'retrieval') {
        // 来源清单是元数据不是对话内容：折叠为单行摘要
        const div = document.createElement('div');
        div.className = 'retrieval-summary';
        const n = (item.results || []).length;
        div.innerHTML = `<span style="color:var(--fg3)">📎 检索来源清单（${n} 条，run 结束时注入）</span>`;
        div.onclick = () => {
          const detail = document.createElement('details');
          detail.open = true;
          detail.innerHTML = '<pre>' + esc(JSON.stringify(item.results, null, 2).slice(0, 8000)) + '</pre>';
          div.replaceWith(detail);
        };
        box.appendChild(div);
      } else if (item.kind === 'tool') {
        const div = document.createElement('div');
        div.className = 'tool-call';
        const summary = toolSummary(item.tool, item.input);
        const fullIn = item.input ? JSON.stringify(item.input, null, 2) : '';
        const statusCls = item.status === 'error' ? ' error' : '';
        div.innerHTML = `<div class="tool-row" onclick="this.parentElement.classList.toggle('open')">
            <span style="color:var(--fg3)">▶</span><span class="tool-name">${esc(item.tool)}</span>
            <span class="tool-summary" title="${esc(summary)}">${esc(summary)}</span>
            ${item.status ? `<span class="tool-status${statusCls}">${esc(item.status)}</span>` : ''}</div>
          <div class="tool-detail">
            ${item.error ? `<div class="tool-label" style="color:var(--error)">Error</div><pre style="color:var(--error)">${esc(item.error)}</pre>` : ''}
            <div class="tool-label">Input</div><pre>${esc(fullIn && fullIn !== summary ? fullIn : '')}</pre>
            <div class="tool-label">Output</div><pre>${esc((item.output||'').slice(0,12000))}</pre>
          </div>`;
        box.appendChild(div);
      } else if (item.kind === 'reasoning') {
        const div = document.createElement('div');
        div.className = 'reasoning-block';
        div.innerHTML = `<div class="reasoning-toggle" onclick="this.nextElementSibling.classList.toggle('open')">▶ Reasoning（${item.text.length} 字）</div>
          <div class="reasoning-content">${renderMd(item.text.slice(0,8000))}</div>`;
        box.appendChild(div);
      } else if (item.kind === 'usage') {
        const u = item.usage;
        const div = document.createElement('div');
        div.className = 'usage-marker';
        // ttft_ms 落库口径是各次调用求和（与前端 statsFormat.ts 一致），展示除以 steps 得平均首字延迟
        const ttftAvg = u.ttft_ms > 0 && u.steps > 0 ? (u.ttft_ms / u.steps / 1000).toFixed(1) + 's' : '-';
        div.textContent = `⚡ ${u.steps||'-'} steps · in ${fmt(u.input_tokens)} · out ${fmt(u.output_tokens)} · cache ${fmt(u.cache_read_tokens)} · llm ${((u.llm_ms||0)/1000).toFixed(0)}s · ttft(平均) ${ttftAvg}`;
        box.appendChild(div);
      }
    }
  }
  const bar = document.getElementById('statsBar');
  bar.style.display = '';
  bar.innerHTML = `<div>Messages: <b>${d.messages.length}</b></div>
    <div>Tools: <b>${d.stats.tool_count}</b></div>
    <div>Reasoning: <b>${d.stats.reasoning_count}</b></div>
    <div>Input Tokens: <b>${fmt(d.stats.input_tokens)}</b></div>
    <div>Output Tokens: <b>${fmt(d.stats.output_tokens)}</b></div>
    <div>Cache Read: <b>${fmt(d.stats.cache_read_tokens)}</b></div>
    <div>LLM Time: <b>${((d.stats.llm_ms||0)/1000).toFixed(0)}s</b></div>`;
  box.scrollTop = 0;
}

function toolSummary(name, input) {
  if (!input || typeof input !== 'object') return '';
  if (['Bash','execute','exec_command','shell'].includes(name)) {
    if (typeof input === 'string') return input.cmd || input;
    return input.command || input.cmd || input.description || '';
  }
  if (name === 'read_file' || name === 'read') return input.file_path || input.filePath || '';
  if (name === 'write_file' || name === 'write') return `${input.file_path || input.filePath || ''} (${(input.content||'').split('\n').length} lines)`;
  if (['web_search','search'].includes(name)) return input.query || '';
  if (['web_fetch','open_page'].includes(name)) return input.url || '';
  if (name === 'retrieval') return input.query || '';
  if (['start_async_task','start_task'].includes(name)) return input.description || '';
  if (['check_async_task','check_task'].includes(name)) return input.task_id || '';
  if (name === 'search_memory') return input.query || '';
  if (name === 'search_history') return input.query || '';
  const s = JSON.stringify(input); return s.length > 120 ? s.slice(0,120) : s;
}

// ---- Theme ----
function setTheme(name) {
  document.documentElement.setAttribute('data-theme', name);
  document.querySelectorAll('.theme-dot').forEach(d => {
    d.classList.toggle('active',
      (d.title || '').toLowerCase().includes(name.split('-')[0].slice(0,4)));
  });
  try { localStorage.setItem('session-viewer-theme', name); } catch {}
}
(function() {
  try {
    const s = localStorage.getItem('session-viewer-theme');
    setTheme(s || 'glassmorphism');
  } catch { setTheme('glassmorphism'); }
})();

boot();
</script>
</body>
</html>"""


# ---------------------------------------------------------------- Provider 基类与统一契约

class Provider:
    """数据源适配器。实现两个方法并注册到 PROVIDERS：

    - list_sessions() -> {"count", "total_messages", "sessions": [session…]}
    - get_messages(session_id) -> {"session_id", "title", "messages", "stats"}

    契约形状见模块 docstring。归一化不了的细节可放进 item 的扩展键，
    客户端按 kind 渲染、未知键忽略。
    """

    id: str = "?"
    label: str = "?"

    def list_sessions(self) -> dict:
        raise NotImplementedError

    def get_messages(self, session_id: str) -> dict:
        raise NotImplementedError


def _empty_stats() -> dict:
    return {"tool_count": 0, "reasoning_count": 0,
            "input_tokens": 0.0, "output_tokens": 0.0,
            "cache_read_tokens": 0.0, "llm_ms": 0.0}


# ---------------------------------------------------------------- Noesis Provider（Postgres）

_DSN_RE = re.compile(r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)")


class _DbLoop:
    """asyncpg 专用事件循环线程（HTTP handler 线程经 run_coroutine_threadsafe 提交）。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.conn = None

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


class NoesisProvider(Provider):
    id = "noesis"
    label = "Noesis (Postgres)"

    def __init__(self):
        self.db = _DbLoop()
        self.db.run(self._connect())

    @property
    def _dsn(self) -> dict:
        from noesis.storage.postgres.manager import ASYNC_SQLALCHEMY_DATABASE_URL

        m = _DSN_RE.match(ASYNC_SQLALCHEMY_DATABASE_URL)
        if not m:
            raise SystemExit(f"无法解析应用 DSN: {ASYNC_SQLALCHEMY_DATABASE_URL}")
        return dict(user=m.group(1), password=m.group(2), host=m.group(3),
                    port=int(m.group(4)), database=m.group(5))

    async def _connect(self):
        import asyncpg

        self.conn = await asyncpg.connect(**self._dsn)
        await self.conn.fetchrow("SELECT 1")

    # ---- 会话列表 ----
    def list_sessions(self) -> dict:
        return self.db.run(self._list_sessions())

    async def _list_sessions(self) -> dict:
        conn = self.conn
        agg = {r["session_id"]: r for r in await conn.fetch(
            "SELECT m.session_id, count(*) AS n_msgs, "
            "coalesce(sum((m.extra->'usage'->>'input_tokens')::float8),0) AS input_tokens, "
            "coalesce(sum((m.extra->'usage'->>'output_tokens')::float8),0) AS output_tokens "
            "FROM t_chat_message m WHERE m.deleted_at IS NULL GROUP BY 1")}
        tool_counts = {r["session_id"]: r["n"] for r in await conn.fetch(
            "SELECT m.session_id, count(*) AS n FROM t_chat_message m, "
            "json_array_elements(CAST(m.content AS json)->'parts') p "
            "WHERE m.deleted_at IS NULL AND p->>'type' IN ('tool','retrieval') GROUP BY 1")}

        sessions = []
        for r in await conn.fetch(
                "SELECT id, parent_id, title, created_at, updated_at, kind FROM t_chat_session "
                "WHERE deleted_at IS NULL ORDER BY updated_at DESC"):
            a = agg.get(r["id"])
            if not a or not a["n_msgs"]:
                continue  # 库中无消息的会话不展示（如旧评测遗留的 drb 空壳行）
            sessions.append({
                "id": r["id"], "parent_id": r["parent_id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"], "kind": r["kind"],
                "n_msgs": a["n_msgs"],
                "input_tokens": a["input_tokens"],
                "output_tokens": a["output_tokens"],
                "tool_count": tool_counts.get(r["id"], 0),
            })
        return {"count": len(sessions),
                "total_messages": sum(s["n_msgs"] for s in sessions),
                "sessions": sessions}

    # ---- 会话消息 ----
    def get_messages(self, session_id: str) -> dict:
        return self.db.run(self._get_messages(session_id))

    async def _get_messages(self, session_id: str) -> dict:
        conn = self.conn
        sess = await conn.fetchrow(
            "SELECT id, title FROM t_chat_session WHERE id = $1", session_id)
        if not sess:
            return {"error": f"session not found: {session_id}"}
        rows = await conn.fetch(
            "SELECT id, role, content::text, extra::text FROM t_chat_message "
            "WHERE session_id = $1 AND deleted_at IS NULL ORDER BY message_sequence",
            session_id)

        messages, stats = [], _empty_stats()
        for r in rows:
            try:
                parts = (json.loads(r["content"]) or {}).get("parts") or []
            except (ValueError, TypeError):
                parts = []
            try:
                extra = json.loads(r["extra"]) if r["extra"] else {}
            except (ValueError, TypeError):
                extra = {}
            if not isinstance(extra, dict):
                extra = {}
            items = []
            for p in parts:
                if not isinstance(p, dict):
                    continue
                t = p.get("type")
                if t == "text" and str(p.get("content") or "").strip():
                    items.append({"kind": "text", "text": str(p["content"])})
                elif t == "reasoning" and str(p.get("content") or "").strip():
                    items.append({"kind": "reasoning", "text": str(p["content"])})
                    stats["reasoning_count"] += 1
                elif t == "tool":
                    items.append({"kind": "tool", "tool": str(p.get("name") or "?"),
                                  "input": p.get("input") or {},
                                  "output": str(p.get("output") or "")})
                    stats["tool_count"] += 1
                elif t == "retrieval":
                    # 来源清单是 run 收尾时注入的元数据：折叠渲染，不计入工具数
                    items.append({"kind": "retrieval",
                                  "results": p.get("results") or []})
            usage = extra.get("usage")
            if usage:
                items.append({"kind": "usage", "usage": usage})
                for k in ("input_tokens", "output_tokens", "cache_read_tokens", "llm_ms"):
                    stats[k] += float(usage.get(k) or 0)
            messages.append({"id": r["id"], "role": r["role"],
                             "origin": extra.get("origin"), "items": items})
        return {"session_id": session_id, "title": sess["title"],
                "messages": messages, "stats": stats}


# ---------------------------------------------------------------- opencode Provider（SQLite .db）

class OpencodeProvider(Provider):
    """opencode 的 session/message/part SQLite 库（即 trace_view.html 的数据源）。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def _row(self, sql, args=()):
        return self._conn.execute(sql, args).fetchall()

    def list_sessions(self) -> dict:
        sess = self._row("SELECT id, title, parent_id, time_created FROM session ORDER BY time_created")
        n_msgs = dict(self._row("SELECT session_id, count(*) FROM message GROUP BY 1"))
        tokens = {}
        for sid, ti, to in self._row(
                "SELECT p.session_id, "
                "sum(json_extract(p.data,'$.tokens.input')), "
                "sum(json_extract(p.data,'$.tokens.output')) "
                "FROM part p WHERE json_extract(p.data,'$.type')='step-finish' GROUP BY 1"):
            tokens[sid] = (ti or 0, to or 0)
        tool_counts = dict(self._row(
            "SELECT session_id, count(*) FROM part "
            "WHERE json_extract(data,'$.type')='tool' GROUP BY 1"))
        sessions = [{
            "id": r[0], "parent_id": r[2], "title": r[1] or "",
            "created_at": r[3], "kind": "root",
            "n_msgs": n_msgs.get(r[0], 0),
            "input_tokens": tokens.get(r[0], (0, 0))[0],
            "output_tokens": tokens.get(r[0], (0, 0))[1],
            "tool_count": tool_counts.get(r[0], 0),
        } for r in sess]
        return {"count": len(sessions),
                "total_messages": sum(s["n_msgs"] for s in sessions),
                "sessions": sessions}

    def get_messages(self, session_id: str) -> dict:
        title_row = self._row("SELECT title FROM session WHERE id = ?", (session_id,))
        if not title_row:
            return {"error": f"session not found: {session_id}"}
        stats = _empty_stats()
        messages = []
        for mid, mdata in self._row(
                "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
                (session_id,)):
            role = (json.loads(mdata).get("role") if mdata else "") or "unknown"
            items = []
            for (pdata,) in self._row(
                    "SELECT data FROM part WHERE message_id = ? ORDER BY time_created", (mid,)):
                p = json.loads(pdata) if pdata else {}
                t = p.get("type")
                if t == "text" and str(p.get("text") or p.get("content") or "").strip():
                    items.append({"kind": "text", "text": str(p.get("text") or p.get("content"))})
                elif t == "reasoning" and str(p.get("text") or p.get("content") or "").strip():
                    items.append({"kind": "reasoning", "text": str(p.get("text") or p.get("content"))})
                    stats["reasoning_count"] += 1
                elif t == "tool":
                    state = p.get("state") or {}
                    items.append({"kind": "tool", "tool": str(p.get("tool") or "?"),
                                  "input": state.get("input") or {},
                                  "output": str(state.get("output") or ""),
                                  "status": state.get("status") or "",
                                  "error": state.get("error") or ""})
                    stats["tool_count"] += 1
                elif t == "step-finish":
                    tk = p.get("tokens") or {}
                    usage = {
                        "steps": 1,
                        "input_tokens": tk.get("input") or 0,
                        "output_tokens": tk.get("output") or 0,
                        "cache_read_tokens": (tk.get("cache") or {}).get("read") or 0,
                        "llm_ms": None, "ttft_ms": None,
                    }
                    if usage["input_tokens"] or usage["output_tokens"]:
                        items.append({"kind": "usage", "usage": usage})
                        stats["input_tokens"] += usage["input_tokens"]
                        stats["output_tokens"] += usage["output_tokens"]
                        stats["cache_read_tokens"] += usage["cache_read_tokens"]
            messages.append({"id": mid, "role": role, "origin": None, "items": items})
        return {"session_id": session_id, "title": title_row[0][0] or "",
                "messages": messages, "stats": stats}


# ---------------------------------------------------------------- codex Provider（rollout JSONL 目录）

class CodexProvider(Provider):
    """codex CLI 的 rollout 会话文件（~/.codex/sessions/<YYYY/MM/DD>/rollout-*.jsonl）。

    一个 rollout 文件 = 一个会话；user/assistant 消息、function_call /
    web_search_call → 工具调用、reasoning → 折叠块、token_count → 用量。
    """

    def __init__(self, sessions_dir: Path):
        self.dir = sessions_dir
        self._files: dict[str, Path] = {}
        for f in glob.glob(str(sessions_dir / "**" / "rollout-*.jsonl"), recursive=True):
            self._files[Path(f).stem] = Path(f)

    def _parse(self, path: Path) -> dict:
        meta, events = {}, []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        for e in events:
            if e.get("type") == "session_meta":
                meta = e.get("payload") or {}
                break
        return meta, events

    def list_sessions(self) -> dict:
        sessions = []
        for sid, path in self._files.items():
            try:
                meta, events = self._parse(path)
            except OSError:
                continue
            ts = None
            m = re.search(r"rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})", path.stem)
            if m:
                try:
                    ts = int(time.mktime(time.strptime(
                        f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}",
                        "%Y-%m-%d %H:%M:%S")) * 1000)
                except ValueError:
                    ts = None
            n_tools = sum(1 for e in events if (e.get("payload") or {}).get("type") in
                          ("function_call", "web_search_call"))
            usage = self._total_usage(events)
            # 标题：取第一条真实用户消息
            title = ""
            for e in events:
                p = e.get("payload") or {}
                if e.get("type") == "event_msg" and p.get("type") == "user_message":
                    msg = str(p.get("message") or "")
                    if msg and not msg.startswith("<"):
                        title = msg.splitlines()[0][:80]
                        break
                if e.get("type") == "response_item" and p.get("type") == "message" \
                        and p.get("role") == "user":
                    texts = [str(c.get("text") or "") for c in (p.get("content") or [])
                             if isinstance(c, dict)]
                    msg = "\n".join(texts)
                    if msg and not msg.startswith("<") and not msg.startswith("#"):
                        title = msg.splitlines()[0][:80]
                        break
            sessions.append({
                "id": sid, "parent_id": None, "title": title or str(meta.get("cwd", sid)),
                "created_at": ts or int(path.stat().st_mtime * 1000), "kind": "root",
                "n_msgs": sum(
                    1 for e in events
                    if (e.get("payload") or {}).get("type") in ("user_message", "agent_message")
                    or (e.get("type") == "response_item"
                        and (e.get("payload") or {}).get("type") == "message"
                        and (e.get("payload") or {}).get("role") in ("user", "assistant"))),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "tool_count": n_tools,
            })
        sessions.sort(key=lambda s: s["created_at"] or 0, reverse=True)
        return {"count": len(sessions),
                "total_messages": sum(s["n_msgs"] for s in sessions),
                "sessions": sessions}

    @staticmethod
    def _total_usage(events: list) -> dict:
        # total_token_usage 本身是会话内累计值：取末次快照，不得逐次加总
        total = {"input_tokens": 0, "output_tokens": 0}
        for e in events:
            p = e.get("payload") or {}
            if e.get("type") == "event_msg" and p.get("type") == "token_count":
                u = (p.get("info") or {}).get("total_token_usage") or {}
                if u:
                    total = {"input_tokens": u.get("input_tokens") or 0,
                             "output_tokens": u.get("output_tokens") or 0}
        return total

    def get_messages(self, session_id: str) -> dict:
        path = self._files.get(session_id)
        if not path:
            return {"error": f"session not found: {session_id}"}
        meta, events = self._parse(path)
        stats = _empty_stats()
        messages = []
        # 按 response_item/event_msg 混排还原顺序：直接按文件顺序消费
        i = 0
        cur_assistant: list | None = None

        def flush():
            nonlocal cur_assistant
            if cur_assistant and cur_assistant["items"]:
                messages.append(cur_assistant)
            cur_assistant = None

        for e in events:
            et = e.get("type")
            p = e.get("payload") or {}
            pt = p.get("type") or ""
            if et == "event_msg" and pt == "user_message":
                flush()
                msg = str(p.get("message") or "")
                if msg and not msg.startswith("<"):
                    messages.append({"id": f"u{len(messages)}", "role": "user",
                                     "origin": None, "items": [{"kind": "text", "text": msg}]})
            elif et == "response_item" and pt == "reasoning":
                text = str(p.get("summary") or p.get("content") or "")
                # codex reasoning 的正文在 summary 列表里
                if isinstance(p.get("summary"), list):
                    text = " ".join(str(s.get("text") or "") for s in p["summary"])
                if text.strip():
                    if cur_assistant is None:
                        cur_assistant = {"id": f"a{len(messages)}", "role": "assistant",
                                         "origin": None, "items": []}
                    cur_assistant["items"].append({"kind": "reasoning", "text": text})
                    stats["reasoning_count"] += 1
            elif et == "response_item" and pt == "function_call":
                if cur_assistant is None:
                    cur_assistant = {"id": f"a{len(messages)}", "role": "assistant",
                                     "origin": None, "items": []}
                try:
                    args = json.loads(p.get("arguments") or "{}")
                except ValueError:
                    args = {"raw": p.get("arguments")}
                cur_assistant["items"].append({
                    "kind": "tool", "tool": str(p.get("name") or "exec"),
                    "input": args, "output": "", "_pending": True,
                    "_call_id": p.get("call_id") or p.get("id")})
                stats["tool_count"] += 1
            elif et == "response_item" and pt == "function_call_output":
                call_id = p.get("call_id")
                for m_ in messages + ([cur_assistant] if cur_assistant else []):
                    for it in (m_ or {}).get("items", []):
                        if it.get("kind") == "tool" and it.get("_call_id") == call_id:
                            out = p.get("output")
                            if isinstance(out, dict):
                                out = out.get("content") or out.get("output") or json.dumps(out, ensure_ascii=False)
                            it["output"] = str(out or "")
                            it.pop("_pending", None)
            elif et == "response_item" and pt == "web_search_call":
                if cur_assistant is None:
                    cur_assistant = {"id": f"a{len(messages)}", "role": "assistant",
                                     "origin": None, "items": []}
                action = p.get("action") or {}
                cur_assistant["items"].append({
                    "kind": "tool", "tool": "web_search",
                    "input": {"query": action.get("query") or action.get("url"),
                              "action": action.get("type")},
                    "output": str(p.get("status") or ""),
                    "status": str(p.get("status") or "")})
                stats["tool_count"] += 1
            elif et == "response_item" and pt == "message":
                role = p.get("role")
                if role not in ("user", "assistant"):
                    continue
                # content 为 [{type: input_text/output_text, text}] 列表；
                # 系统注入（<skills_instructions> 等）跳过
                texts = [str(c.get("text") or "") for c in (p.get("content") or [])
                         if isinstance(c, dict)]
                text = "\n\n".join(t2 for t2 in texts if t2.strip())
                if not text.strip() or text.startswith("<"):
                    continue
                flush()
                messages.append({"id": f"m{len(messages)}", "role": role,
                                 "origin": None, "items": [{"kind": "text", "text": text}]})
            elif et == "event_msg" and pt == "agent_message":
                if cur_assistant is None:
                    cur_assistant = {"id": f"a{len(messages)}", "role": "assistant",
                                     "origin": None, "items": []}
                cur_assistant["items"].append({"kind": "text", "text": str(p.get("message") or "")})
            elif et == "event_msg" and pt == "token_count":
                u = (p.get("info") or {}).get("total_token_usage") or {}
                usage = {
                    "steps": u.get("total_hybrid_llm_calls"),
                    "input_tokens": u.get("input_tokens") or 0,
                    "output_tokens": u.get("output_tokens") or 0,
                    "cache_read_tokens": u.get("cached_input_tokens") or 0,
                    "llm_ms": None, "ttft_ms": None,
                }
                # 只在该会话末次 token_count 报一次（避免每步重复）
                self._last_usage = usage
            i += 1
        flush()
        if getattr(self, "_last_usage", None):
            if messages and messages[-1]["role"] == "assistant":
                messages[-1]["items"].append({"kind": "usage", "usage": self._last_usage})
            stats.update({k: self._last_usage.get(k) or 0
                          for k in ("input_tokens", "output_tokens", "cache_read_tokens")})
            self._last_usage = None
        return {"session_id": session_id, "title": str(meta.get("cwd", session_id)),
                "messages": messages, "stats": stats}
# ---------------------------------------------------------------- HTTP 服务

PROVIDERS: dict[str, Provider] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/api/sources":
            data = {"sources": [{"id": p.id, "label": p.label} for p in PROVIDERS.values()]}
            self._send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json")
        elif url.path == "/api/sessions":
            provider = PROVIDERS.get((q.get("source") or [""])[0])
            if not provider:
                self._send(400, b'{"error": "unknown source"}', "application/json")
                return
            self._send(200, json.dumps(provider.list_sessions(), ensure_ascii=False).encode(),
                       "application/json")
        elif url.path == "/api/messages":
            provider = PROVIDERS.get((q.get("source") or [""])[0])
            sid = (q.get("session") or [""])[0]
            if not provider or not sid:
                self._send(400, b'{"error": "source and session required"}', "application/json")
                return
            self._send(200, json.dumps(provider.get_messages(sid), ensure_ascii=False).encode(),
                       "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="多源会话轨迹查看器（裸命令即全量加载：noesis 数据库必挂，"
                    "codex 检测到 ~/.codex/sessions 自动挂载）")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--opencode", action="append", default=[],
                    metavar="DB_PATH", help="额外挂 opencode SQLite .db（可多次）")
    ap.add_argument("--codex", action="append", default=[],
                    metavar="SESSIONS_DIR", help="额外挂 codex 会话目录（可多次）")
    args = ap.parse_args()

    errors = []
    try:
        p = NoesisProvider()
        PROVIDERS[p.id] = p
    except Exception as exc:  # noqa: BLE001
        errors.append(f"noesis: {exc}")
    for i, db_path in enumerate(args.opencode):
        try:
            p = OpencodeProvider(Path(db_path).expanduser())
            p.id = f"opencode-{i}" if i else "opencode"
            p.label = f"opencode ({Path(db_path).name})"
            PROVIDERS[p.id] = p
        except Exception as exc:  # noqa: BLE001
            errors.append(f"opencode {db_path}: {exc}")
    codex_dirs = [Path(s).expanduser() for s in args.codex]
    default_codex = Path.home() / ".codex" / "sessions"
    if default_codex.is_dir() and not any(d.resolve() == default_codex.resolve() for d in codex_dirs):
        codex_dirs.insert(0, default_codex)
    for i, sdir in enumerate(codex_dirs):
        if not sdir.is_dir():
            continue
        try:
            p = CodexProvider(Path(sdir).expanduser())
            p.id = f"codex-{i}" if i else "codex"
            p.label = f"codex ({Path(sdir).name})"
            PROVIDERS[p.id] = p
        except Exception as exc:  # noqa: BLE001
            errors.append(f"codex {sdir}: {exc}")

    if not PROVIDERS:
        for e in errors:
            print(f"源加载失败: {e}", file=__import__("sys").stderr)
        raise SystemExit("没有任何可用数据源")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Session Trace Viewer: {url}（Ctrl+C 退出）")
    print("已挂载源:", ", ".join(p.label for p in PROVIDERS.values()))
    for e in errors:
        print(f"  [警告] 源加载失败: {e}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
