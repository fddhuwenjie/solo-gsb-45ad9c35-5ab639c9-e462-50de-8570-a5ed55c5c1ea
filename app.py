# -*- coding: utf-8 -*-
"""针织行式编译台 —— Flask 服务。

路由：
    GET  /              单页界面
    POST /api/compile   编译全部行，返回逐行展开/错误信息

请求体：
    {"castOn": 24,
     "definitions": ["edge = k k", "band = (@edge, p p)x2"],
     "rows": ["@edge, *k2tog, yo*", ...]}
"""
from flask import Flask, jsonify, render_template, request

import compiler

app = Flask(__name__)


@app.get('/')
def index():
    return render_template('index.html')


@app.post('/api/compile')
def api_compile():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': '请求体必须是 JSON 对象'}), 400

    cast_on = data.get('castOn')
    rows = data.get('rows')
    definitions = data.get('definitions', [])

    try:
        result = compiler.compile_rows(cast_on, rows, definitions)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    return jsonify(result)


@app.get('/api/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    # 本地开发：python app.py  →  http://localhost:8000
    app.run(host='0.0.0.0', port=8000, debug=False)
