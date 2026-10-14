"""Web application for searching biosimilar products by ingredient or product name,
downloading regulatory/clinical documents, and browsing the collected SQLite database.

Run:
    uv run python -m product_search.app --port 8080
"""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from product_search import discover
from product_search import collect_products

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

# Global job runner state
_job_lock = threading.Lock()
_current_job: dict = {
    "status": "idle",  # "idle" | "running" | "completed" | "failed"
    "started_at": None,
    "finished_at": None,
    "products": [],
    "logs": [],
    "metrics": None,
    "error": None,
}


class LogInterceptor:
    """Tee stdout to memory buffer for real-time web logs."""
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout

    def write(self, s):
        if self.original_stdout:
            self.original_stdout.write(s)
            self.original_stdout.flush()
        if s.strip():
            with _job_lock:
                timestamp = time.strftime("%H:%M:%S")
                for line in s.splitlines():
                    if line.strip():
                        _current_job["logs"].append(f"[{timestamp}] {line.strip()}")
                        if len(_current_job["logs"]) > 500:
                            _current_job["logs"].pop(0)

    def flush(self):
        if self.original_stdout:
            self.original_stdout.flush()


def run_collection_async(products: list[dict]):
    def worker():
        old_stdout = sys.stdout
        interceptor = LogInterceptor(old_stdout)
        sys.stdout = interceptor
        try:
            with _job_lock:
                _current_job["status"] = "running"
                _current_job["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _current_job["finished_at"] = None
                _current_job["products"] = [p["product_id"] for p in products]
                _current_job["logs"] = [f"[{time.strftime('%H:%M:%S')}] 수집 시작: {len(products)}개 제품"]
                _current_job["metrics"] = None
                _current_job["error"] = None

            collector = collect_products.run(products)

            with _job_lock:
                _current_job["status"] = "completed"
                _current_job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _current_job["metrics"] = collector.metrics
                _current_job["logs"].append(f"[{time.strftime('%H:%M:%S')}] 수집 완료: 문서 {len(collector.documents)}개, 페이지 {sum(d.get('page_count', 0) for d in collector.documents)}개")
        except Exception as exc:
            with _job_lock:
                _current_job["status"] = "failed"
                _current_job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _current_job["error"] = str(exc)
                _current_job["logs"].append(f"[{time.strftime('%H:%M:%S')}] 오류 발생: {exc}")
        finally:
            sys.stdout = old_stdout

    t = threading.Thread(target=worker, daemon=True)
    t.start()


class RequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.serve_ui()
        elif path == "/api/search":
            self.api_search(query)
        elif path == "/api/status":
            self.api_status()
        elif path == "/api/catalog":
            self.api_catalog()
        elif path == "/api/documents":
            self.api_documents(query)
        elif path.startswith("/data/"):
            # Serve raw files safely
            self.serve_data_file(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/collect":
            self.api_collect()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def serve_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api_search(self, query):
        q = query.get("q", [""])[0].strip()
        if not q:
            self.serve_json({"error": "검색어를 입력해주세요."}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            res = discover.search(q)
            self.serve_json(res)
        except Exception as e:
            self.serve_json({"error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def api_status(self):
        with _job_lock:
            state = dict(_current_job)
        self.serve_json(state)

    def api_catalog(self):
        products_file = DATA_DIR / "catalog/products.jsonl"
        docs_file = DATA_DIR / "catalog/documents.jsonl"
        studies_file = DATA_DIR / "catalog/studies.jsonl"
        db_file = DATA_DIR / "collection.sqlite"

        products = discover.read_jsonl(products_file)
        docs = discover.read_jsonl(docs_file)
        studies = discover.read_jsonl(studies_file)

        counts = {}
        if db_file.exists():
            try:
                conn = sqlite3.connect(db_file)
                for tbl in ["products", "studies", "document_versions", "pages"]:
                    counts[tbl] = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
                conn.close()
            except Exception:
                pass

        self.serve_json({
            "products_count": len(products),
            "documents_count": len(docs),
            "studies_count": len(studies),
            "database_counts": counts,
            "products": products,
            "sqlite_exists": db_file.exists(),
            "sqlite_bytes": db_file.stat().st_size if db_file.exists() else 0,
        })

    def api_documents(self, query):
        product_id = query.get("product_id", [""])[0].strip()
        docs_file = DATA_DIR / "catalog/documents.jsonl"
        docs = discover.read_jsonl(docs_file)
        if product_id:
            docs = [d for d in docs if d.get("product_id") == product_id]
        self.serve_json({"documents": docs})

    def api_collect(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            self.serve_json({"error": "Invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
            return

        with _job_lock:
            if _current_job["status"] == "running":
                self.serve_json({"error": "이미 다른 수집 작업이 진행 중입니다."}, status=HTTPStatus.CONFLICT)
                return

        products = req.get("products", [])
        queries = req.get("queries", [])

        if not products and queries:
            # Resolve queries
            for q in queries:
                res = discover.search(q)
                products.extend(res.get("products", []))

        if not products:
            self.serve_json({"error": "수집할 대상 제품이 없습니다."}, status=HTTPStatus.BAD_REQUEST)
            return

        run_collection_async(products)
        self.serve_json({"message": f"{len(products)}개 제품 수집을 시작했습니다.", "products": [p.get("product_id") for p in products]})

    def serve_data_file(self, path):
        rel_path = path.lstrip("/")
        file_path = HERE / rel_path
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        # Security: check within data dir
        try:
            file_path.resolve().relative_to(DATA_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "Access denied")
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = "application/octet-stream"

        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'inline; filename="{file_path.name}"')
        self.end_headers()
        self.wfile.write(content)

    def serve_ui(self):
        html = UI_HTML.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


UI_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>바이오시밀러 규제 데이터 수집 & 다운로드</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen">
  <div class="max-w-7xl mx-auto px-4 py-8">
    <!-- Header -->
    <header class="mb-8 border-b border-slate-200 pb-5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-lg bg-indigo-600 text-white flex items-center justify-center font-bold text-xl shadow-sm">
            <i class="fa-solid fa-dna"></i>
          </div>
          <div>
            <h1 class="text-2xl font-bold text-slate-900 tracking-tight">Biosimilar Regulatory Collector</h1>
            <p class="text-sm text-slate-500">성분명·제품명 검색 기반 글로벌 규제기관(EMA·FDA·Health Canada·CT.gov) 데이터 자동 수집 & 적재</p>
          </div>
        </div>
        <div id="catalogBadge" class="text-xs bg-white border border-slate-200 rounded-full px-4 py-1.5 shadow-sm flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <span>적재 DB: 제품 <b id="dbProdCount">0</b>개 | 문서 <b id="dbDocCount">0</b>건</span>
        </div>
      </div>
    </header>

    <!-- Search Section -->
    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
      <h2 class="text-base font-semibold text-slate-900 mb-2">바이오시밀러 검색</h2>
      <p class="text-xs text-slate-500 mb-4">
        성분명(예: <code class="bg-slate-100 text-indigo-600 px-1.5 py-0.5 rounded cursor-pointer" onclick="quickSearch('ustekinumab')">ustekinumab</code>, <code class="bg-slate-100 text-indigo-600 px-1.5 py-0.5 rounded cursor-pointer" onclick="quickSearch('denosumab')">denosumab</code>) 또는 
        제품명(예: <code class="bg-slate-100 text-indigo-600 px-1.5 py-0.5 rounded cursor-pointer" onclick="quickSearch('yesintek')">yesintek</code>, <code class="bg-slate-100 text-indigo-600 px-1.5 py-0.5 rounded cursor-pointer" onclick="quickSearch('usymro')">usymro</code>, <code class="bg-slate-100 text-indigo-600 px-1.5 py-0.5 rounded cursor-pointer" onclick="quickSearch('pyzchiva')">pyzchiva</code>)을 입력하세요.
      </p>

      <form id="searchForm" class="flex gap-2" onsubmit="event.preventDefault(); doSearch();">
        <div class="relative flex-1">
          <i class="fa-solid fa-magnifying-glass absolute left-3.5 top-3.5 text-slate-400"></i>
          <input type="text" id="searchInput" placeholder="성분명 또는 제품명을 입력하세요..." 
                 class="w-full pl-10 pr-4 py-2.5 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm">
        </div>
        <button type="submit" id="searchBtn" class="bg-indigo-600 hover:bg-indigo-700 text-white font-medium px-6 py-2.5 rounded-lg text-sm transition flex items-center gap-2 shadow-sm">
          <span>검색</span>
        </button>
      </form>
    </div>

    <!-- Active Collection Status Banner -->
    <div id="statusBanner" class="hidden bg-slate-900 text-slate-100 rounded-xl p-5 mb-6 shadow-md transition-all">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-3">
          <span id="statusSpinner" class="animate-spin text-indigo-400 text-lg"><i class="fa-solid fa-circle-notch"></i></span>
          <div>
            <h3 class="font-semibold text-sm" id="statusTitle">수집 작업 진행 중...</h3>
            <p class="text-xs text-slate-400" id="statusSubtitle">백그라운드에서 공공 API 및 규제 PDF를 다운로드 및 파싱하고 있습니다.</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span id="statusTag" class="text-xs px-2.5 py-1 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">RUNNING</span>
        </div>
      </div>
      <div class="bg-black/40 rounded-lg p-3 font-mono text-xs text-slate-300 max-h-40 overflow-y-auto" id="logBox">
        <!-- Logs live here -->
      </div>
    </div>

    <!-- Results Section -->
    <div id="resultsCard" class="hidden bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-3">
          <h2 class="text-base font-semibold text-slate-900">검색된 제품 목록</h2>
          <span id="searchKindBadge" class="text-xs px-2.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200">성분명 검색</span>
          <span id="activeIngredientName" class="text-xs text-slate-500"></span>
        </div>
        <div class="flex items-center gap-2">
          <button id="collectSelectedBtn" onclick="collectSelected()" class="bg-emerald-600 hover:bg-emerald-700 text-white font-medium px-4 py-2 rounded-lg text-xs transition flex items-center gap-1.5 shadow-sm">
            <i class="fa-solid fa-download"></i>
            <span>선택한 제품 일괄 수집/다운로드</span>
          </button>
        </div>
      </div>

      <div class="overflow-x-auto border border-slate-200 rounded-lg">
        <table class="w-full text-left text-xs divide-y divide-slate-200">
          <thead class="bg-slate-50 text-slate-600 font-semibold">
            <tr>
              <th class="p-3 w-10 text-center"><input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAll(this)" class="rounded text-indigo-600 focus:ring-indigo-500"></th>
              <th class="p-3">Product ID / 개발코드</th>
              <th class="p-3">EMA (유럽)</th>
              <th class="p-3">FDA (미국 브랜드)</th>
              <th class="p-3">Health Canada</th>
              <th class="p-3">개발사 / 허가권자</th>
              <th class="p-3">임상시험 (NCT ID)</th>
              <th class="p-3 text-center">수집 상태</th>
              <th class="p-3 text-center">작업</th>
            </tr>
          </thead>
          <tbody id="productsTableBody" class="divide-y divide-slate-100">
            <!-- Dynamic rows -->
          </tbody>
        </table>
      </div>
    </div>

    <!-- Collected Catalog / Documents Section -->
    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-base font-semibold text-slate-900">수집된 데이터 카탈로그 & 문서 열람</h2>
          <p class="text-xs text-slate-500">로컬 SQLite DB 및 원본 raw 파일(PDF, HTML, JSON) 보관 현황</p>
        </div>
        <button onclick="loadCatalog()" class="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded-lg flex items-center gap-1">
          <i class="fa-solid fa-rotate-right"></i>
          <span>새로고침</span>
        </button>
      </div>

      <div id="catalogList" class="space-y-3">
        <!-- Products cards -->
      </div>
    </div>
  </div>

  <script>
    let searchResults = [];

    function quickSearch(val) {
      document.getElementById('searchInput').value = val;
      doSearch();
    }

    async function doSearch() {
      const q = document.getElementById('searchInput').value.trim();
      if (!q) return;
      const btn = document.getElementById('searchBtn');
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>검색 중...</span>';

      try {
        const res = await fetch('/api/search?q=' + encodeURIComponent(q));
        const data = await res.json();
        if (data.error) {
          alert('검색 오류: ' + data.error);
          return;
        }
        renderSearchResults(data);
      } catch (e) {
        alert('검색 실패: ' + e.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>검색</span>';
      }
    }

    function renderSearchResults(data) {
      searchResults = data.products || [];
      const card = document.getElementById('resultsCard');
      const tbody = document.getElementById('productsTableBody');
      const badge = document.getElementById('searchKindBadge');
      const ingSpan = document.getElementById('activeIngredientName');

      card.classList.remove('hidden');
      badge.textContent = data.kind === 'ingredient' ? '성분명 검색' : '제품명 검색';
      badge.className = data.kind === 'ingredient' 
        ? 'text-xs px-2.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200'
        : 'text-xs px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200';
      ingSpan.textContent = data.ingredient ? `(성분: ${data.ingredient})` : '';

      tbody.innerHTML = '';
      if (searchResults.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="p-6 text-center text-slate-400">일치하는 바이오시밀러 제품을 찾지 못했습니다.</td></tr>';
        return;
      }

      searchResults.forEach((p, idx) => {
        const tr = document.createElement('tr');
        tr.className = 'hover:bg-slate-50 transition';
        const isCollected = p.collected;

        const studyLinks = (p.study_ids || []).map(id => 
          `<a href="https://clinicaltrials.gov/study/${id}" target="_blank" class="text-indigo-600 hover:underline mr-1.5"><i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i> ${id}</a>`
        ).join('') || '<span class="text-slate-400">-</span>';

        tr.innerHTML = `
          <td class="p-3 text-center"><input type="checkbox" value="${idx}" class="prod-check rounded text-indigo-600 focus:ring-indigo-500"></td>
          <td class="p-3">
            <div class="font-bold text-slate-900">${p.product_id}</div>
            <div class="text-[11px] text-slate-500">${p.development_code ? '코드: ' + p.development_code : ''}</div>
          </td>
          <td class="p-3">
            <span class="font-medium">${p.ema_name || '<span class="text-slate-400">-</span>'}</span>
            ${p.ema_epar ? '<span class="ml-1 text-[10px] bg-blue-50 text-blue-700 px-1 rounded border border-blue-200">EPAR</span>' : ''}
          </td>
          <td class="p-3">
            <span class="font-medium">${p.us_brand || '<span class="text-slate-400">-</span>'}</span>
          </td>
          <td class="p-3">
            <span>${p.ca_brand || '<span class="text-slate-400">-</span>'}</span>
          </td>
          <td class="p-3 max-w-xs truncate" title="${p.company || ''}">
            ${p.company || '-'}
          </td>
          <td class="p-3">
            <div class="flex flex-wrap gap-1">${studyLinks}</div>
          </td>
          <td class="p-3 text-center">
            ${isCollected 
              ? '<span class="text-[11px] bg-emerald-50 text-emerald-700 font-medium px-2 py-0.5 rounded-full border border-emerald-200"><i class="fa-solid fa-check"></i> 수집완료</span>'
              : '<span class="text-[11px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full border border-slate-200">미수집</span>'
            }
          </td>
          <td class="p-3 text-center">
            <button onclick="collectSingle(${idx})" class="bg-indigo-600 hover:bg-indigo-700 text-white text-[11px] font-medium px-2.5 py-1 rounded transition shadow-xs flex items-center gap-1 mx-auto">
              <i class="fa-solid fa-download"></i>
              <span>수집</span>
            </button>
          </td>
        `;
        tbody.appendChild(tr);
      });
    }

    function toggleSelectAll(master) {
      document.querySelectorAll('.prod-check').forEach(cb => cb.checked = master.checked);
    }

    async function collectSingle(idx) {
      const prod = searchResults[idx];
      if (!prod) return;
      if (!confirm(`제품 '${prod.product_id}' (${prod.ema_name || prod.us_brand})의 규제·임상 문서를 수집하시겠습니까?`)) return;
      await sendCollectRequest([prod]);
    }

    async function collectSelected() {
      const checkedBoxes = Array.from(document.querySelectorAll('.prod-check:checked'));
      if (checkedBoxes.length === 0) {
        alert('수집할 제품을 선택해주세요.');
        return;
      }
      const selectedProds = checkedBoxes.map(cb => searchResults[parseInt(cb.value)]);
      if (!confirm(`선택한 ${selectedProds.length}개 제품의 데이터를 수집하시겠습니까?`)) return;
      await sendCollectRequest(selectedProds);
    }

    async function sendCollectRequest(products) {
      try {
        const res = await fetch('/api/collect', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({products})
        });
        const data = await res.json();
        if (data.error) {
          alert('수집 요청 오류: ' + data.error);
          return;
        }
        showStatusBanner();
      } catch (e) {
        alert('요청 실패: ' + e.message);
      }
    }

    function showStatusBanner() {
      document.getElementById('statusBanner').classList.remove('hidden');
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        const state = await res.json();
        const banner = document.getElementById('statusBanner');
        const title = document.getElementById('statusTitle');
        const tag = document.getElementById('statusTag');
        const spinner = document.getElementById('statusSpinner');
        const logBox = document.getElementById('logBox');

        if (state.status === 'idle') {
          if (state.logs && state.logs.length > 0) {
            banner.classList.remove('hidden');
            tag.textContent = 'IDLE';
            tag.className = 'text-xs px-2.5 py-1 rounded bg-slate-700 text-slate-300';
            spinner.innerHTML = '<i class="fa-regular fa-clock"></i>';
            spinner.classList.remove('animate-spin');
          } else {
            banner.classList.add('hidden');
          }
        } else if (state.status === 'running') {
          banner.classList.remove('hidden');
          title.textContent = `수집 진행 중 (${state.products.join(', ')})`;
          tag.textContent = 'RUNNING';
          tag.className = 'text-xs px-2.5 py-1 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30';
          spinner.innerHTML = '<i class="fa-solid fa-circle-notch"></i>';
          spinner.classList.add('animate-spin');
        } else if (state.status === 'completed') {
          banner.classList.remove('hidden');
          title.textContent = `수집 완료 (${state.products.join(', ')})`;
          tag.textContent = 'COMPLETED';
          tag.className = 'text-xs px-2.5 py-1 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
          spinner.innerHTML = '<i class="fa-solid fa-circle-check text-emerald-400"></i>';
          spinner.classList.remove('animate-spin');
        } else if (state.status === 'failed') {
          banner.classList.remove('hidden');
          title.textContent = `수집 실패: ${state.error || ''}`;
          tag.textContent = 'FAILED';
          tag.className = 'text-xs px-2.5 py-1 rounded bg-rose-500/20 text-rose-300 border border-rose-500/30';
          spinner.innerHTML = '<i class="fa-solid fa-circle-exclamation text-rose-400"></i>';
          spinner.classList.remove('animate-spin');
        }

        if (state.logs && state.logs.length > 0) {
          logBox.innerHTML = state.logs.map(l => `<div>${l}</div>`).join('');
          logBox.scrollTop = logBox.scrollHeight;
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function loadCatalog() {
      try {
        const res = await fetch('/api/catalog');
        const data = await res.json();
        document.getElementById('dbProdCount').textContent = data.products_count || 0;
        document.getElementById('dbDocCount').textContent = data.documents_count || 0;

        const listDiv = document.getElementById('catalogList');
        listDiv.innerHTML = '';

        if (!data.products || data.products.length === 0) {
          listDiv.innerHTML = '<div class="text-sm text-slate-400 p-4 border border-dashed rounded-lg text-center">아직 수집된 제품이 없습니다. 상단에서 검색 후 수집을 실행해보세요.</div>';
          return;
        }

        for (const p of data.products) {
          const card = document.createElement('div');
          card.className = 'border border-slate-200 rounded-lg p-4 bg-slate-50/50 hover:bg-slate-50 transition';
          card.innerHTML = `
            <div class="flex items-center justify-between">
              <div>
                <span class="font-bold text-slate-900">${p.product_id}</span>
                <span class="text-xs text-slate-500 ml-2">EMA: ${p.ema_name || '-'} | US: ${p.us_brand || '-'} | CA: ${p.ca_brand || '-'}</span>
              </div>
              <button onclick="toggleDocViewer('${p.product_id}', this)" class="text-xs bg-white border border-slate-200 hover:bg-slate-100 px-2.5 py-1 rounded shadow-xs text-indigo-600 font-medium">
                수집 문서 보기 (${p.product_id})
              </button>
            </div>
            <div id="docs_${p.product_id}" class="hidden mt-3 pt-3 border-t border-slate-200">
              <div class="text-xs text-slate-400">문서 불러오는 중...</div>
            </div>
          `;
          listDiv.appendChild(card);
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function toggleDocViewer(prodId, btn) {
      const container = document.getElementById('docs_' + prodId);
      if (!container.classList.contains('hidden')) {
        container.classList.add('hidden');
        btn.textContent = `수집 문서 보기 (${prodId})`;
        return;
      }
      container.classList.remove('hidden');
      btn.textContent = `문서 닫기 (${prodId})`;

      try {
        const res = await fetch('/api/documents?product_id=' + encodeURIComponent(prodId));
        const data = await res.json();
        const docs = data.documents || [];
        if (docs.length === 0) {
          container.innerHTML = '<div class="text-xs text-slate-400">수집된 문서가 없습니다.</div>';
          return;
        }
        let html = '<div class="space-y-1.5">';
        for (const d of docs) {
          const rawLink = d.raw_path ? `/${d.raw_path}` : null;
          html += `
            <div class="flex items-center justify-between text-xs bg-white p-2 rounded border border-slate-200">
              <div class="flex items-center gap-2 max-w-xl truncate">
                <span class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-700">${d.source}</span>
                <span class="font-medium text-slate-800 truncate" title="${d.title}">${d.title}</span>
                <span class="text-slate-400 text-[11px]">(${d.page_count || 0}쪽)</span>
              </div>
              <div class="flex items-center gap-2">
                ${rawLink ? `<a href="${rawLink}" target="_blank" class="text-indigo-600 hover:underline flex items-center gap-1"><i class="fa-regular fa-file-pdf"></i> 원본 다운로드</a>` : ''}
              </div>
            </div>
          `;
        }
        html += '</div>';
        container.innerHTML = html;
      } catch (e) {
        container.innerHTML = `<div class="text-xs text-rose-500">문서 로드 실패: ${e.message}</div>`;
      }
    }

    // Initial load & Polling
    loadCatalog();
    pollStatus();
    setInterval(pollStatus, 2000);
    setInterval(loadCatalog, 5000);
  </script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080, help="Web port (default: 8080)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    args = parser.parse_args()

    server_address = (args.host, args.port)
    httpd = ThreadingHTTPServer(server_address, RequestHandler)
    print(f"Biosimilar Collection Web Server running at http://localhost:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer shutting down.")


if __name__ == "__main__":
    main()
