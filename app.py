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
# 1. BANCO DE DADOS E INICIALIZAÇÃO
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
            
            cursor.execute("SELECT COUNT(*) FROM Valores;")
            if cursor.fetchone()[0] == 0:
                dados_teste = [
                    (date.today().isoformat(), "Salário Mensal", "receita", 5000.00),
                    (date.today().isoformat(), "Supermercado", "despesa", 450.50),
                    (date.today().isoformat(), "Conta de Luz", "despesa", 120.30)
                ]
                cursor.executemany("""
                    INSERT INTO Valores (data, descricao, tipo, valor)
                    VALUES (?, ?, ?, ?);
                """, dados_teste)
            conn.commit()
    except sqlite3.Error as e:
        print(f"Erro no banco de dados: {e}")

init_db()

# ==========================================
# 2. FERRAMENTAS E MÉTODOS OPERACIONAIS
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
    """Gera o total consolidado agrupando por descrição para um determinado tipo."""
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
        print(f"Erro no relatório: {e}")
        return []

# ==========================================
# 3. ROTAS E INTERFACE WEB
# ==========================================

@app.get("/", response_class=HTMLResponse)
def home_ui():
    registros = consultar_controle()
    resumo = calcular_saldo_final()
    relatorio_despesas = relatorio_por_categoria('despesa')
    
    # Tabela principal de lançamentos
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
        </tr>
        """

    # Tabela do relatório por tipo de despesa
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
    </head>
    <body class="bg-light">
        <div class="container py-5">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h2>📊 Sistema de Controle Financeiro</h2>
                <a href="/exportar-csv" class="btn btn-outline-success">📥 Exportar para CSV</a>
            </div>
            
            <!-- Cards de Saldo -->
            <div class="row mb-4">
                <div class="col-md-4">
                    <div class="card text-white bg-success mb-3 shadow-sm">
                        <div class="card-body">
                            <h5 class="card-title">Receitas Totais</h5>
                            <h3>R$ {resumo['total_receita']:.2f}</h3>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card text-white bg-danger mb-3 shadow-sm">
                        <div class="card-body">
                            <h5 class="card-title">Despesas Totais</h5>
                            <h3>R$ {resumo['total_despesa']:.2f}</h3>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card text-white bg-primary mb-3 shadow-sm">
                        <div class="card-body">
                            <h5 class="card-title">Saldo Final</h5>
                            <h3>R$ {resumo['saldo_final']:.2f}</h3>
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
                            <input type="text" name="descricao" class="form-control" placeholder="Ex: Mercado, Aluguel..." required>
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

            <!-- Relatório Agrupado por Tipo de Despesa -->
            <div class="card mb-4 shadow-sm">
                <div class="card-header bg-white"><strong>📉 Relatório Consolidação por Categoria de Despesa</strong></div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead class="table-light">
                            <tr>
                                <th>Descrição / Categoria</th>
                                <th>Quantidade de Lançamentos</th>
                                <th>Total Acumulado</th>
                            </tr>
                        </thead>
                        <tbody>
                            {linhas_relatorio if linhas_relatorio else "<tr><td colspan='3' class='text-center text-muted'>Nenhuma despesa registrada.</td></tr>"}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Tabela Geral de Extrato -->
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
                            </tr>
                        </thead>
                        <tbody>
                            {linhas_tabela}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post("/adicionar")
def adicionar_registro(descricao: str = Form(...), valor: float = Form(...), tipo: str = Form(...)):
    calculo(descricao=descricao, tipo=tipo, valor=Decimal(str(valor)))
    return HTMLResponse(content="<script>window.location.href='/';</script>")


@app.get("/exportar-csv")
def exportar_csv():
    """Gera e faz o download automático de um arquivo CSV contendo todo o histórico."""
    registros = consultar_controle()
    
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=';')
    
    # Cabeçalho do arquivo CSV
    writer.writerow(["ID", "Data", "Descricao", "Tipo", "Valor (R$)"])
    
    for r in registros:
        writer.writerow([r['id'], r['data'], r['descricao'], r['tipo'], f"{r['valor']:.2f}".replace('.', ',')])
        
    stream.seek(0)
    
    response = StreamingResponse(
        iter([stream.getvalue()]),
        media_type="text/csv"
    )
    response.headers["Content-Disposition"] = "attachment; filename=extrato_financeiro.csv"
    return response
