import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path
import tempfile
from fpdf import FPDF

from criar_banco import processar_xml, salvar_no_banco

st.set_page_config(page_title="VirtuAr - Gestão de Compras e Orçamentos", layout="wide")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "compras_nfe.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

# ================= MENU LATERAL COM LOGO E SESSÃO =================
if (BASE_DIR / "logo.png").exists():
    st.sidebar.image("logo.png", use_container_width=True)

st.sidebar.markdown("---")

if "usuario_logado" not in st.session_state:
    st.session_state["usuario_logado"] = "VirtuAr (Equipe)"

st.sidebar.success(f"👤 Conectado como: **{st.session_state['usuario_logado']}**")
st.sidebar.markdown("---")

menu = st.sidebar.radio(
    "Navegação", 
    [
        "📊 Dashboard Inicial", 
        "🔍 Consultas e Filtros", 
        "📤 Upload de XML",
        "💰 Calculadora de Preços",
        "📄 Cotação / Orçamento"
    ]
)

# ================= TELA 1: DASHBOARD =================
if menu == "📊 Dashboard Inicial":
    st.title("Dashboard de Compras")
    st.write("Resumo geral das notas fiscais e custos da empresa.")
    conn = get_connection()
    hoje = datetime.now()
    mes_atual = hoje.strftime("%m")
    ano_atual = hoje.strftime("%Y")

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(valor_total) FROM notas_fiscais WHERE strftime('%Y-%m', data_emissao) = ?", (f"{ano_atual}-{mes_atual}",))
    res_mes = cursor.fetchone()
    qtd_notas_mes = res_mes[0] if res_mes[0] else 0
    total_gasto_mes = res_mes[1] if res_mes[1] else 0.0
    
    cursor.execute("SELECT COUNT(*), SUM(valor_total) FROM notas_fiscais")
    res_geral = cursor.fetchone()
    qtd_notas_total = res_geral[0] if res_geral[0] else 0
    total_gasto_geral = res_geral[1] if res_geral[1] else 0.0

    col1, col2, col3 = st.columns(3)
    col1.metric("Gasto no Mês Atual", f"R$ {total_gasto_mes:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    col2.metric("Notas Lançadas no Mês", qtd_notas_mes)
    col3.metric("Total Histórico Acumulado", f"R$ {total_gasto_geral:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.divider()
    st.subheader("Últimas 5 Notas Lançadas")
    df_ultimas = pd.read_sql_query("""
        SELECT strftime('%d/%m/%Y', n.data_emissao) AS Data, f.nome AS Fornecedor, n.numero_nf AS NF, n.valor_total AS Total
        FROM notas_fiscais n
        JOIN fornecedores f ON n.cnpj_fornecedor = f.cnpj
        ORDER BY n.data_emissao DESC LIMIT 5
    """, conn)
    st.dataframe(df_ultimas, use_container_width=True, hide_index=True)
    conn.close()

# ================= TELA 2: CONSULTAS =================
elif menu == "🔍 Consultas e Filtros":
    st.title("Consulta de Peças e Preços")
    conn = get_connection()
    df_fornecedores = pd.read_sql_query("SELECT DISTINCT nome FROM fornecedores ORDER BY nome", conn)
    lista_fornecedores = ["Todos"] + df_fornecedores["nome"].tolist()
    
    col_busca1, col_busca2, col_busca3 = st.columns(3)
    termo_pesquisa = col_busca1.text_input("🔍 Nome da Peça (ex: PRESSOSTATO)")
    fornecedor_selecionado = col_busca2.selectbox("🏢 Fornecedor", lista_fornecedores)
    meses = {"Todos": "", "Janeiro": "01", "Fevereiro": "02", "Março": "03", "Abril": "04", "Maio": "05", "Junho": "06", "Julho": "07", "Agosto": "08", "Setembro": "09", "Outubro": "10", "Novembro": "11", "Dezembro": "12"}
    mes_selecionado = col_busca3.selectbox("📅 Mês da Compra", list(meses.keys()))

    query = """
    SELECT strftime('%d/%m/%Y', n.data_emissao) AS "Data", f.nome AS "Fornecedor", i.descricao AS "Produto", i.quantidade AS "Qtd", i.valor_unitario AS "Preço Un. (R$)", i.valor_total AS "Total (R$)"
    FROM itens_nota i
    JOIN notas_fiscais n ON i.chave_nfe = n.chave_nfe
    JOIN fornecedores f ON n.cnpj_fornecedor = f.cnpj
    WHERE 1=1
    """
    params = []
    if termo_pesquisa:
        query += " AND i.descricao LIKE ?"
        params.append(f"%{termo_pesquisa.upper()}%")
    if fornecedor_selecionado != "Todos":
        query += " AND f.nome = ?"
        params.append(fornecedor_selecionado)
    if mes_selecionado != "Todos":
        query += " AND strftime('%m', n.data_emissao) = ?"
        params.append(meses[mes_selecionado])
    query += " ORDER BY n.data_emissao DESC"
    
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()

    st.write(f"**Resultados encontrados:** {len(df)}")
    if not df.empty:
        if termo_pesquisa:
            menor_preco = df["Preço Un. (R$)"].min()
            st.success(f"💡 O menor preço encontrado nesta busca foi **R$ {menor_preco:.2f}**")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.warning("Nenhum registro encontrado.")

# ================= TELA 3: UPLOAD =================
elif menu == "📤 Upload de XML":
    st.title("Importar Novas Notas Fiscais")
    st.write("Arraste os arquivos XML para adicionar compras ao banco de dados.")
    arquivos = st.file_uploader("Solte os arquivos XML aqui", type=["xml"], accept_multiple_files=True)
    if arquivos:
        if st.button("Processar e Salvar"):
            sucessos = 0
            barra = st.progress(0)
            for idx, arquivo in enumerate(arquivos):
                try:
                    dados = processar_xml(arquivo)
                    if dados:
                        salvar_no_banco(dados)
                        sucessos += 1
                except Exception as e:
                    st.error(f"Erro na nota {arquivo.name}: {e}")
                barra.progress((idx + 1) / len(arquivos))
            st.success(f"✅ Sucesso! {sucessos} nota(s) importada(s) para o banco.")

# ================= TELA 4: CALCULADORA =================
elif menu == "💰 Calculadora de Preços":
    st.title("Calculadora de Preços (Espelho da Planilha)")
    st.write("Cálculo exato de Markup Reverso considerando comissões, impostos e custos de frete por peso.")

    conn = get_connection()
    df_produtos = pd.read_sql_query("SELECT DISTINCT descricao FROM itens_nota ORDER BY descricao", conn)
    lista_produtos = ["Digitar valor manualmente..."] + df_produtos["descricao"].tolist()
    
    st.subheader("1. Produto e Custo")
    produto_selecionado = st.selectbox("Selecione a peça para puxar o custo:", lista_produtos)
    
    custo_sugerido = 0.0
    if produto_selecionado != "Digitar valor manualmente...":
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(valor_unitario) FROM itens_nota WHERE descricao = ?", (produto_selecionado,))
        resultado = cursor.fetchone()
        if resultado and resultado[0]:
            custo_sugerido = resultado[0]
            st.info(f"💡 Menor custo registrado: **R$ {custo_sugerido:.2f}**")
    conn.close()

    col_c1, col_c2 = st.columns(2)
    custo_produto = col_c1.number_input("Custo da Peça (R$)", min_value=0.0, value=float(custo_sugerido), step=1.0)
    peso_produto = col_c2.number_input("Peso (kg)", min_value=0.0, value=1.0, step=0.1)

    st.subheader("2. Taxas e Parâmetros (%)")
    col_t1, col_t2, col_t3 = st.columns(3)
    tipo_anuncio = col_t1.selectbox("Tipo de Anúncio", ["PREMIUM", "CLASSICO", "SHOPPE", "LOJA"])
    
    if tipo_anuncio == "PREMIUM":
        comissao_padrao, frete_tab_padrao, frete_sup_padrao = 21.11, 7.95, 13.25
    elif tipo_anuncio == "CLASSICO":
        comissao_padrao, frete_tab_padrao, frete_sup_padrao = 16.11, 7.95, 13.25
    elif tipo_anuncio == "SHOPPE":
        comissao_padrao, frete_tab_padrao, frete_sup_padrao = 23.50, 5.00, 5.00
    else:
        comissao_padrao, frete_tab_padrao, frete_sup_padrao = 21.39, 0.50, 0.50

    taxa_comissao = col_t1.number_input("Taxa de Comissão (%)", min_value=0.0, value=float(comissao_padrao), step=0.01)
    imposto_governo = col_t2.number_input("Imposto Governo (%)", min_value=0.0, value=10.0, step=0.1)
    margem_liquida = col_t3.number_input("Margem Líquida Desejada (%)", min_value=0.0, value=15.0, step=1.0)

    st.subheader("3. Custos de Frete (R$)")
    col_f1, col_f2, col_f3 = st.columns(3)
    frete_tabela = col_f1.number_input("Frete Tabela (> R$79)", min_value=0.0, value=float(frete_tab_padrao), step=0.5)
    super_frete = col_f2.number_input("Super Frete (< R$79)", min_value=0.0, value=float(frete_sup_padrao), step=0.5)
    custo_flex = col_f3.number_input("Flex (Motoboy)", min_value=0.0, value=12.99, step=0.5)

    if st.button("Calcular Preços Exatos", type="primary"):
        soma_percentuais = (taxa_comissao + imposto_governo + margem_liquida) / 100
        if soma_percentuais >= 1:
            st.error("Erro: A soma das porcentagens ultrapassa 100%.")
        elif custo_produto == 0:
            st.warning("Insira um custo válido.")
        else:
            divisor = 1 - soma_percentuais
            preco_sem_frete = (custo_produto + super_frete) / divisor
            preco_com_frete = (custo_produto + frete_tabela) / divisor
            preco_flex = (custo_produto + custo_flex) / divisor
            
            st.markdown("---")
            st.markdown("### 🎯 Preços de Venda Sugeridos")
            c1, c2, c3 = st.columns(3)
            c1.success(f"**SEM FRETE GRÁTIS**\n## R$ {preco_sem_frete:.2f}")
            c2.info(f"**COM FRETE GRÁTIS**\n## R$ {preco_com_frete:.2f}")
            c3.warning(f"**VENDEDOR FLEX**\n## R$ {preco_flex:.2f}")

# ================= TELA 5: COTAÇÃO / ORÇAMENTO PROFISSIONAL =================
elif menu == "📄 Cotação / Orçamento":
    st.title("Emissão de Cotação e Orçamento Profissional")
    st.write("Preencha os dados do cliente, adicione as peças e gere um PDF moderno e elegante.")

    st.subheader("1. Dados do Cliente e Logística")
    col_c1, col_c2, col_c3 = st.columns(3)
    nome_cliente = col_c1.text_input("👤 Nome / Razão Social", "Cliente Balcão")
    cnpj_cliente = col_c2.text_input("📄 CPF / CNPJ", "00.000.000/0001-00")
    tel_cliente = col_c3.text_input("📞 Telefone / WhatsApp", "(31) 9____-____")

    col_e1, col_e2, col_e3 = st.columns(3)
    end_cliente = col_e1.text_input("🏠 Endereço", "Rua Principal, 100")
    cid_cliente = col_e2.text_input("🏙️ Cidade / UF", "Contagem - MG")
    cep_cliente = col_e3.text_input("📮 CEP", "32000-000")

    col_l1, col_l2, col_l3 = st.columns(3)
    transportadora = col_l1.text_input("🚚 Transportadora", "Correios / Retirada")
    cond_pagamento = col_l2.selectbox("💳 Condição de Pagamento", ["À vista", "Boleto Bancário", "PIX", "Cartão de Crédito", "25/50/75/100"])
    vendedor = col_l3.text_input("👔 Vendedor Responsável", "VirtuAr Compressores")

    st.divider()
    st.subheader("2. Adicionar Produtos ao Orçamento")
    
    conn = get_connection()
    df_produtos = pd.read_sql_query("SELECT DISTINCT descricao FROM itens_nota ORDER BY descricao", conn)
    conn.close()
    lista_prods = df_produtos["descricao"].tolist() if not df_produtos.empty else []

    if "itens_orcamento" not in st.session_state:
        st.session_state["itens_orcamento"] = []

    if lista_prods:
        with st.form("form_orcamento_item", clear_on_submit=True):
            col_i1, col_i2, col_i3 = st.columns([3, 1, 1])
            prod_escolhido = col_i1.selectbox("Selecione a Peça no Histórico", lista_prods)
            qtd_item = col_i2.number_input("Quantidade", min_value=1, value=1)
            preco_item = col_i3.number_input("Preço Unitário Sugerido (R$)", min_value=0.0, value=100.0, step=10.0)
            
            btn_add = st.form_submit_button("➕ Adicionar Item na Cotação")
            if btn_add:
                st.session_state["itens_orcamento"].append({
                    "produto": prod_escolhido,
                    "quantidade": qtd_item,
                    "preco_unitario": preco_item,
                    "total": qtd_item * preco_item
                })
                st.success("Item adicionado com sucesso!")

    if st.session_state["itens_orcamento"]:
        st.write("#### 🛒 Itens Selecionados na Cotação")
        df_carrinho = pd.DataFrame(st.session_state["itens_orcamento"])
        st.dataframe(df_carrinho, use_container_width=True, hide_index=True)
        
        total_geral = df_carrinho["total"].sum()
        st.markdown(f"### 💰 **VALOR TOTAL DA COTAÇÃO: R$ {total_geral:,.2f}**".replace(",", "X").replace(".", ",").replace("X", "."))

        col_b1, col_b2 = st.columns(2)
        if col_b1.button("🗑️ Limpar Lista de Itens"):
            st.session_state["itens_orcamento"] = []
            st.rerun()

        if col_b2.button("📥 Gerar PDF Moderno da Cotação", type="primary"):
            pdf = FPDF()
            pdf.add_page()
            
            # --- CABEÇALHO MODERNO COM LOGO ---
            logo_path = BASE_DIR / "logo.png"
            if logo_path.exists():
                # Insere a logo no topo esquerdo (X=10, Y=10, Largura=35)
                pdf.image(str(logo_path), x=10, y=10, w=35)
                
            # Dados da empresa no topo direito
            pdf.set_xy(50, 10)
            pdf.set_font("Arial", "B", 13)
            pdf.set_text_color(20, 50, 120)  # Azul corporativo moderno
            pdf.cell(150, 6, "VIRTUAR COMPRESSORES E PECAS", 0, 1, "R")
            
            pdf.set_x(50)
            pdf.set_font("Arial", "", 8)
            pdf.set_text_color(80, 80, 80)
            pdf.cell(150, 4, "Rua Manoel Teixeira de Camargos, n 90 - Loja 1 - Bairro Gloria - Contagem/MG", 0, 1, "R")
            pdf.set_x(50)
            pdf.cell(150, 4, "Tel: (31) 2888-0533 / (31) 98288-1653 | www.VirtuArCompressores.com.br", 0, 1, "R")
            
            # Linha divisória elegante
            pdf.set_draw_color(30, 90, 160)
            pdf.set_line_width(0.8)
            pdf.line(10, 28, 200, 28)
            
            pdf.ln(8)
            
            # --- BLOCO DE TÍTULO DA COTAÇÃO ---
            pdf.set_fill_color(30, 90, 160) # Fundo azul moderno
            pdf.set_text_color(255, 255, 255) # Texto branco
            pdf.set_font("Arial", "B", 10)
            pdf.cell(190, 7, "  COTACAO DE VENDA / ORCAMENTO", 1, 1, "L", True)
            
            # --- BLOCO DE DADOS DO CLIENTE E LOGÍSTICA ---
            pdf.set_fill_color(245, 247, 250) # Fundo cinza claro moderno
            pdf.set_text_color(30, 30, 30)
            pdf.set_font("Arial", "", 8.5)
            
            # Caixa estilizada para os dados
            y_inicio = pdf.get_y()
            pdf.rect(10, y_inicio, 190, 25, "F")
            
            pdf.set_xy(12, y_inicio + 2)
            pdf.cell(95, 5, f"Cliente: {nome_cliente}", 0, 0)
            pdf.cell(95, 5, f"CPF/CNPJ: {cnpj_cliente}", 0, 1)
            
            pdf.set_x(12)
            pdf.cell(95, 5, f"Endereco: {end_cliente} - {cid_cliente} - CEP: {cep_cliente}", 0, 0)
            pdf.cell(95, 5, f"Telefone: {tel_cliente}", 0, 1)
            
            pdf.set_x(12)
            pdf.cell(95, 5, f"Transportadora: {transportadora}", 0, 0)
            pdf.cell(95, 5, f"Cond. Pagamento: {cond_pagamento}", 0, 1)
            
            pdf.set_x(12)
            pdf.cell(95, 5, f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}", 0, 0)
            pdf.cell(95, 5, f"Vendedor: {vendedor}", 0, 1)
            
            pdf.ln(8)
            
            # --- TABELA DE PRODUTOS ---
            # Cabeçalho da Tabela
            pdf.set_fill_color(40, 40, 40) # Cinza escuro elegante
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Arial", "B", 9)
            pdf.cell(100, 7, "  Descricao do Item", 0, 0, "L", True)
            pdf.cell(20, 7, "Qtd", 0, 0, "C", True)
            pdf.cell(35, 7, "Preco Unit.", 0, 0, "R", True)
            pdf.cell(35, 7, "Total  ", 0, 1, "R", True)
            
            # Linhas dos Itens
            pdf.set_font("Arial", "", 8.5)
            pdf.set_text_color(40, 40, 40)
            
            preencher = False
            for item in st.session_state["itens_orcamento"]:
                # Alterna cor de fundo das linhas para facilitar a leitura (estilo moderno)
                if preencher:
                    pdf.set_fill_color(248, 249, 250)
                else:
                    pdf.set_fill_color(255, 255, 255)
                    
                pdf.cell(100, 6, f"  {str(item['produto'][:48])}", 1, 0, "L", True)
                pdf.cell(20, 6, str(item["quantidade"]), 1, 0, "C", True)
                pdf.cell(35, 6, f"R$ {item['preco_unitario']:.2f} ", 1, 0, "R", True)
                pdf.cell(35, 6, f"R$ {item['total']:.2f} ", 1, 1, "R", True)
                preencher = not preencher
                
            pdf.ln(4)
            
            # --- RODAPÉ DE VALOR TOTAL ---
            pdf.set_fill_color(235, 240, 245)
            pdf.set_font("Arial", "B", 11)
            pdf.set_text_color(20, 50, 120)
            pdf.cell(190, 9, f"VALOR TOTAL GERAL: R$ {total_geral:,.2f}   ", 1, 1, "R", True)
            
            # Salva PDF Temporário
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                pdf.output(tmp_file.name)
                with open(tmp_file.name, "rb") as f_pdf:
                    bytes_pdf = f_pdf.read()
                    
            st.download_button(
                label="📥 Baixar PDF Moderno Oficial para WhatsApp",
                data=bytes_pdf,
                file_name=f"Cotacao_{nome_cliente.replace(' ', '_')}.pdf",
                mime="application/pdf",
                type="primary"
            )