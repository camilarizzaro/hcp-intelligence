from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import os, requests, json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hcp-intelligence-secret-2025')

SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://zopmwlgnfxdqvhodwnlp.supabase.co')
SUPABASE_ANON = os.environ.get('SUPABASE_ANON_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpvcG13bGduZnhkcXZob2R3bmxwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzg3Nzc2NjYsImV4cCI6MjA5NDM1MzY2Nn0.fRpHsf5oYeDk3BWdreSJg0bs9Q5rVar3Zg1FdumFTKo')
SUPABASE_SVC  = os.environ.get('SUPABASE_SERVICE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpvcG13bGduZnhkcXZob2R3bmxwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODc3NzY2NiwiZXhwIjoyMDk0MzUzNjY2fQ.AWsPABR0AREa0mP1-6A3pCsINp7wvTEj2ZIVpo6Qh7M')

def svc_headers():
    return {'apikey': SUPABASE_SVC, 'Authorization': f'Bearer {SUPABASE_SVC}', 'Content-Type': 'application/json'}

def anon_headers(token=None):
    t = token or SUPABASE_ANON
    return {'apikey': SUPABASE_ANON, 'Authorization': f'Bearer {t}', 'Content-Type': 'application/json'}

# ─── AUTH ───────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    r = requests.post(f'{SUPABASE_URL}/auth/v1/token?grant_type=password',
        headers=anon_headers(), json={'email': data['email'], 'password': data['password']})
    resp = r.json()
    if r.status_code != 200:
        return jsonify({'error': resp.get('error_description', 'Credenciais inválidas')}), 401
    access_token = resp['access_token']
    user_id = resp['user']['id']
    # buscar perfil
    pr = requests.get(f'{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=*',
        headers=svc_headers())
    profiles = pr.json()
    role = profiles[0]['role'] if profiles else 'viewer'
    full_name = profiles[0].get('full_name','') if profiles else ''
    session['user_id'] = user_id
    session['email'] = data['email']
    session['role'] = role
    session['full_name'] = full_name
    session['token'] = access_token
    return jsonify({'role': role, 'full_name': full_name})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
def api_me():
    if 'user_id' not in session:
        return jsonify({'error': 'not authenticated'}), 401
    return jsonify({'email': session['email'], 'role': session['role'], 'full_name': session['full_name']})

# ─── DADOS ──────────────────────────────────────────────
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'não autenticado'}), 401
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'não autenticado'}), 401
        if session.get('role') != 'admin':
            return jsonify({'error': 'acesso negado'}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/api/hcps')
@require_auth
def api_hcps():
    all_rows = []
    offset = 0
    limit = 1000
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/hcps?select=*&offset={offset}&limit={limit}',
            headers=svc_headers())
        batch = r.json()
        if not isinstance(batch, list) or len(batch) == 0:
            break
        all_rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return jsonify(all_rows)

@app.route('/api/dataset')
@require_auth
def api_dataset():
    r = requests.get(f'{SUPABASE_URL}/rest/v1/datasets?select=*&order=created_at.desc&limit=1',
        headers=svc_headers())
    return jsonify(r.json())

@app.route('/api/upload', methods=['POST'])
@require_admin
def api_upload():
    import io
    data = request.json
    rows = data.get('rows', [])
    nome = data.get('nome', 'upload')
    if not rows:
        return jsonify({'error': 'Nenhum dado recebido'}), 400
    # Deletar dados antigos
    requests.delete(f'{SUPABASE_URL}/rest/v1/hcps?id=gt.0', headers=svc_headers())
    # Inserir em lotes
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        r = requests.post(f'{SUPABASE_URL}/rest/v1/hcps',
            headers={**svc_headers(), 'Prefer': 'return=minimal'},
            json=batch)
        if r.status_code not in (200, 201):
            return jsonify({'error': f'Erro ao inserir lote {i}: {r.text}'}), 500
    # Registrar dataset
    requests.post(f'{SUPABASE_URL}/rest/v1/datasets',
        headers={**svc_headers(), 'Prefer': 'return=minimal'},
        json={'nome': nome, 'total_registros': len(rows), 'uploaded_by': session['user_id']})
    return jsonify({'ok': True, 'total': len(rows)})

@app.route('/api/delete-dataset', methods=['POST'])
@require_admin
def api_delete_dataset():
    requests.delete(f'{SUPABASE_URL}/rest/v1/hcps?id=gt.0', headers=svc_headers())
    requests.delete(f'{SUPABASE_URL}/rest/v1/datasets?id=gt.0', headers=svc_headers())
    return jsonify({'ok': True})

# ─── USUÁRIOS ────────────────────────────────────────────
@app.route('/api/users', methods=['GET'])
@require_admin
def api_users():
    r = requests.get(f'{SUPABASE_URL}/rest/v1/profiles?select=*&order=created_at.desc',
        headers=svc_headers())
    return jsonify(r.json())

@app.route('/api/users', methods=['POST'])
@require_admin
def api_create_user():
    data = request.json
    r = requests.post(f'{SUPABASE_URL}/auth/v1/admin/users',
        headers=svc_headers(),
        json={'email': data['email'], 'password': data['password'],
              'email_confirm': True, 'user_metadata': {'full_name': data['full_name']}})
    result = r.json()
    if not result.get('id'):
        return jsonify({'error': result.get('msg', 'Erro ao criar usuário')}), 400
    requests.post(f'{SUPABASE_URL}/rest/v1/profiles',
        headers={**svc_headers(), 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json={'id': result['id'], 'email': data['email'],
              'full_name': data['full_name'], 'role': data.get('role','viewer')})
    return jsonify({'ok': True})

@app.route('/api/users/<uid>', methods=['DELETE'])
@require_admin
def api_delete_user(uid):
    requests.delete(f'{SUPABASE_URL}/auth/v1/admin/users/{uid}', headers=svc_headers())
    requests.delete(f'{SUPABASE_URL}/rest/v1/profiles?id=eq.{uid}', headers=svc_headers())
    return jsonify({'ok': True})

# ─── PÁGINAS ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
