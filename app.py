import os
import io
import csv
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, StreamingResponse

DATA_DIR = os.getenv("RENDER_DISK_PATH", ".")
DB_NAME = os.path.join(DATA_DIR, "controle_financeiro.db")

app = FastAPI(title="Agente Financeiro IA")

# ==========================================
# 1. BANCO DE DADOS E SEED AUTOMÁTICO
# ==========================================

def init_db() -> None:
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS Valores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data TEXT NOT NULL,
                    descricao TEXT NOT NULL,
                    tipo TEXT NOT NULL CHECK(tipo IN ('receita', 'despesa')),
                    valor REAL NOT NULL
                );
            """)
            
            # Popula o banco no Render/Local se estiver vazio
            cursor.execute("SELECT COUNT(*) FROM Valores;")
            if cursor.fetchone()[0] == 0:
                dados_iniciais = [
                    (date.today().isoformat(), "Salário Mensal", "receita", 6500.00),
                    (date.today().isoformat(), "Projeto Freelance", "receita", 1200.00),
                    (date.today().isoformat(), "Supermercado", "despesa", 680.40),
                    (date.today().isoformat(), "Conta de Luz", "despesa", 145.20),
                    (date.today().isoformat(), "Internet Fibra", "despesa", 119.90),
                    (date.today().isoformat(), "Combustível", "despesa", 220.00),
                    (date.today().isoformat(), "Restaurante", "despesa", 185.00)
                ]
                cursor.executemany("""
                    INSERT INTO Valores (data, descricao, tipo, valor)
                    VALUES (?, ?, ?, ?);
                """, dados_iniciais)
            conn.commit()
    except sqlite3.Error as e:
        print(f"Erro na inicialização do banco: {e}")

init_db()

# ==========================================
# 2. METODOS OPERACIONAIS & FERRAMENTAS
# ==========================================

def consultar_controle(
    id: Optional[int] = None,
    data_filtro: Optional[date] = None,
    descricao: Optional[str] = None,
    tipo: Optional[str] = None,
    valor: Optional[Decimal] = None
) -> List[Dict[str, Any]]:
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT id, data, descricao, tipo, valor FROM Valores WHERE 1=1"
            params: List[Any] = []
            
            if id is not None:
                query += " AND id = ?"
                params.append(id)
            if data_filtro is not None:
                query += " AND data = ?"
                params.append(data_filtro.isoformat() if isinstance(data_filtro, date) else data_filtro)
            if descricao:
                query += " AND descricao LIKE ?"
                params.append(f"%{descricao}%")
            if tipo:
                query += " AND tipo = ?"
                params.append(tipo.lower().strip())
            if valor is not None:
                query += " AND valor = ?"
                params.append(float(valor))
                
            query += " ORDER BY id DESC"
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"Erro na consulta: {e}")
        return []


def calculo(
    descricao: str,
    tipo: str,
    valor: Decimal,
    data_transacao: Optional[date] = None
) -> Dict[str, Any]:
    tipo_norm = tipo.lower().strip()
    if tipo_norm not in ('receita', 'despesa'):
        raise ValueError("Tipo deve ser 'receita' ou 'despesa'.")
        
    data_reg = (data_transacao or date.today()).isoformat()
    valor_float = float(valor)
    
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO Valores (data, descricao, tipo, valor)
                VALUES (?, ?, ?, ?);
            """, (data_reg, descricao, tipo_norm, valor_float))
            conn.commit()
            return {"status": "sucesso", "id_registrado": cursor.lastrowid}
    except sqlite3.Error as e:
        return {"status": "erro", "mensagem": str(e)}


def deletar_registro(registro_id: int) -> bool:
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM Valores WHERE id = ?;", (registro_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        print(f"Erro ao deletar registro: {e}")
        return False


def calcular_saldo_final(
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None
) -> Dict[str, Any]:
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            query = "SELECT tipo, SUM(valor) FROM Valores WHERE 1=1"
            params: List[Any] = []
            
            if data_inicio:
                query += " AND data >= ?"
                params.append(data_inicio.isoformat())
            if data_fim:
                query += " AND data <= ?"
                params.append(data_fim.isoformat())
                
            query += " GROUP BY tipo"
            cursor.execute(query, params)
            resultados = dict(cursor.fetchall())
            
            total_receita = Decimal(str(resultados.get("receita", 0.0)))
            total_despesa = Decimal(str(resultados.get("despesa", 0.0)))
            saldo_final = total_receita - total_despesa
            
            return {
                "total_receita": float(total_receita),
                "total_despesa": float(total_despesa),
                "saldo_final": float(saldo_final)
            }
    except sqlite3.Error as e:
        return {"status": "erro", "mensagem": str(e)}


def relatorio_por_categoria(tipo_filtro: str = 'despesa') -> List[Dict[str, Any]]:
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT descricao, SUM(valor) as total, COUNT(*) as qtd
                FROM Valores
                WHERE tipo = ?
                GROUP BY LOWER(descricao)
                ORDER BY total DESC;
            """, (tipo_filtro.lower(),))
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        return []

# ==========================================
# 3. INTERFACE WEB E ROTAS FASTAPI
# ==========================================

@app.get("/", response_class=HTMLResponse)
def home_ui():
    registros = consultar_controle()
    resumo = calcular_saldo_final()
    relatorio_despesas = relatorio_por_categoria('despesa')
    
    # Tabela principal de lançamentos com opção de exclusão
    linhas_tabela = ""
    for r in registros:
        cor_badge = "bg-success" if r['tipo'] == 'receita' else "bg-danger"
        tipo_str = r['tipo'].upper()
        linhas_tabela += f"""
        <tr>
            <td>{r['id']}</td>
            <td>{r['data']}</td>
            <td>{r['descricao']}</td>
            <td><span class="badge {cor_badge}">{tipo_str}</span></td>
            <td>R$ {r['valor']:.2f}</td>
            <td class="text-center">
                <form action="/deletar/{r['id']}" method="post" style="display:inline;">
                    <button type="submit" class="btn btn-sm btn-outline-danger" onclick="return confirm('Deseja excluir este registro?')">🗑️ Excluir</button>
                </form>
            </td>
        </tr>
        """

    # Relatório de despesas
    linhas_relatorio = ""
    for item in relatorio_despesas:
        linhas_relatorio += f"""
        <tr>
            <td>{item['descricao']}</td>
            <td>{item['qtd']}</td>
            <td>R$ {item['total']:.2f}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Controle Financeiro</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body class="bg-light">
        <div class="container py-5">
            <h2 class="mb-4 text-center">📊 Sistema de Controle Financeiro</h2>
            
            <!-- Cards de Saldo e Gráfico de Pizza -->
            <div class="row mb-4 align-items-center">
                <div class="col-md-7">
                    <div class="row">
                        <div class="col-12 mb-3">
                            <div class="card text-white bg-success shadow-sm">
                                <div class="card-body">
                                    <h5 class="card-title">Receitas Totais</h5>
                                    <h3>R$ {resumo['total_receita']:.2f}</h3>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 mb-3">
                            <div class="card text-white bg-danger shadow-sm">
                                <div class="card-body">
                                    <h5 class="card-title">Despesas Totais</h5>
                                    <h3>R$ {resumo['total_despesa']:.2f}</h3>
                                </div>
                            </div>
                        </div>
                        <div class="col-12">
                            <div class="card text-white bg-primary shadow-sm">
                                <div class="card-body">
                                    <h5 class="card-title">Saldo Final</h5>
                                    <h3>R$ {resumo['saldo_final']:.2f}</h3>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Gráfico de Pizza (Chart.js) -->
                <div class="col-md-5">
                    <div class="card shadow-sm p-3 text-center">
                        <h6 class="card-subtitle mb-2 text-muted">Proporção: Receitas x Despesas</h6>
                        <div style="max-width: 280px; margin: 0 auto;">
                            <canvas id="graficoPizza"></canvas>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Formulário de Cadastro -->
            <div class="card mb-4 shadow-sm">
                <div class="card-header bg-white"><strong>Novo Lançamento</strong></div>
                <div class="card-body">
                    <form action="/adicionar" method="post" class="row g-3">
                        <div class="col-md-5">
                            <label class="form-label">Descrição</label>
                            <input type="text" name="descricao" class="form-control" placeholder="Ex: Mercado, Salário..." required>
                        </div>
                        <div class="col-md-3">
                            <label class="form-label">Valor (R$)</label>
                            <input type="number" step="0.01" name="valor" class="form-control" placeholder="0.00" required>
                        </div>
                        <div class="col-md-4">
                            <label class="form-label d-block">Tipo de Operação</label>
                            <div class="btn-group w-100" role="group">
                                <input type="radio" class="btn-check" name="tipo" id="tipo_receita" value="receita" autocomplete="off" checked>
                                <label class="btn btn-outline-success" for="tipo_receita">🟢 Receita</label>

                                <input type="radio" class="btn-check" name="tipo" id="tipo_despesa" value="despesa" autocomplete="off">
                                <label class="btn btn-outline-danger" for="tipo_despesa">🔴 Despesa</label>
                            </div>
                        </div>
                        <div class="col-12 text-end mt-3">
                            <button type="submit" class="btn btn-dark px-4">Salvar Registro</button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Relatório por Categoria com Exportação CSV abaixo -->
            <div class="card mb-4 shadow-sm">
                <div class="card-header bg-white"><strong>📉 Relatório por Categoria de Despesa</strong></div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead class="table-light">
                            <tr>
                                <th>Descrição / Categoria</th>
                                <th>Qtd. Lançamentos</th>
                                <th>Total Acumulado</th>
                            </tr>
                        </thead>
                        <tbody>
                            {linhas_relatorio if linhas_relatorio else "<tr><td colspan='3' class='text-center text-muted'>Nenhuma despesa registrada.</td></tr>"}
                        </tbody>
                    </table>
                </div>
                <div class="card-footer bg-white text-end">
                    <a href="/exportar-csv" class="btn btn-sm btn-outline-success">📥 Exportar Relatório para CSV</a>
                </div>
            </div>

            <!-- Tabela Geral de Extrato com Exclusão -->
            <div class="card shadow-sm">
                <div class="card-header bg-white"><strong>Extrato Completo</strong></div>
                <div class="card-body p-0">
                    <table class="table table-striped mb-0">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Data</th>
                                <th>Descrição</th>
                                <th>Tipo</th>
                                <th>Valor</th>
                                <th class="text-center">Ação</th>
                            </tr>
                        </thead>
                        <tbody>
                            {linhas_tabela}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <script>
            const ctx = document.getElementById('graficoPizza').getContext('2d');
            new Chart(ctx, {{
                type: 'pie',
                data: {{
                    labels: ['Receitas', 'Despesas'],
                    datasets: [{{
                        data: [{resumo['total_receita']}, {resumo['total_despesa']}],
                        backgroundColor: ['#198754', '#dc3545']
                    }}]
                }},
                options: {{
                    responsive: true,
                    plugins: {{
                        legend: {{ position: 'bottom' }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post("/adicionar")
def adicionar_registro(descricao: str = Form(...), valor: float = Form(...), tipo: str = Form(...)):
    calculo(descricao=descricao, tipo=tipo, valor=Decimal(str(valor)))
    return HTMLResponse(content="<script>window.location.href='/';</script>")


@app.post("/deletar/{registro_id}")
def deletar_registro_rota(registro_id: int):
    deletar_registro(registro_id)
    return HTMLResponse(content="<script>window.location.href='/';</script>")


@app.get("/exportar-csv")
def exportar_csv():
    registros = consultar_controle()
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=';')
    
    writer.writerow(["ID", "Data", "Descricao", "Tipo", "Valor (R$)"])
    for r in registros:
        writer.writerow([r['id'], r['data'], r['descricao'], r['tipo'], f"{r['valor']:.2f}".replace('.', ',')])
        
    stream.seek(0)
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=extrato_financeiro.csv"
    return response
