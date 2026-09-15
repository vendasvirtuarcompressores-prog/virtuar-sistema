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
if os_logo := (BASE_DIR / "logo.png").exists():
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
        "📄 Gerar Orçamento" # <-- Nova aba adicionada!
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
            c1.caption("Abaixo de R$ 79,00")
            
            c2.info(f"**COM FRETE GRÁTIS**\n## R$ {preco_com_frete:.2f}")
            c2.caption("Acima de R$ 79,00")
            
            c3.warning(f"**VENDEDOR FLEX**\n## R$ {preco_flex:.2f}")
            c3.caption("Entrega Flex")

# ================= TELA 5: GERAR ORÇAMENTO PDF =================
elif menu == "📄 Gerar Orçamento":
    st.title("Gerador de Orçamentos Profissionais")
    st.write("Monte o orçamento selecionando os produtos cadastrados e gere um PDF pronto para o cliente.")

    col_cl1, col_cl2 = st.columns(2)
    nome_cliente = col_cl1.text_input("👤 Nome do Cliente / Empresa", "Cliente Balcão")
    tel_cliente = col_cl2.text_input("📞 Telefone / WhatsApp", "(31) 9____-____")

    st.subheader("Adicionar Itens ao Orçamento")
    
    conn = get_connection()
    df_produtos = pd.read_sql_query("SELECT DISTINCT descricao FROM itens_nota ORDER BY descricao", conn)
    conn.close()
    
    lista_prods = df_produtos["descricao"].tolist() if not df_produtos.empty else []

    if "itens_orcamento" not in st.session_state:
        st.session_state["itens_orcamento"] = []

    if lista_prods:
        with st.form("form_item"):
            col_i1, col_i2, col_i3 = st.columns([3, 1, 1])
            prod_escolhido = col_i1.selectbox("Produto", lista_prods)
            qtd_item = col_i2.number_input("Quantidade", min_value=1, value=1)
            preco_item = col_i3.number_input("Preço Unitário (R$)", min_value=0.0, value=100.0, step=10.0)
            
            adicionar = st.form_submit_button("➕ Adicionar Item")
            if adicionar:
                st.session_state["itens_orcamento"].escape = True
                st.session_state["itens_orcamento"].append({
                    "produto": prod_escolhido,
                    "quantidade": qtd_item,
                    "preco_unitario": preco_item,
                    "total": qtd_item * preco_item
                })
                st.success(f"Item adicionado!")

    if st.session_state["itens_orcamento"]:
        st.write("#### 🛒 Carrinho do Orçamento")
        df_carrinho = pd.DataFrame(st.session_state["itens_orcamento"])
        st.dataframe(df_carrinho, use_container_width=True, hide_index=True)
        
        valor_total_orcamento = df_carrinho["total"].sum()
        st.markdown(f"### 💰 **Total Geral: R$ {valor_total_orcamento:,.2f}**".replace(",", "X").replace(".", ",").replace("X", "."))

        if st.button("🗑️ Limpar Orçamento"):
            st.session_state["itens_orcamento"] = []
            st.rerun()

        if st.button("📥 Gerar PDF do Orçamento", type="primary"):
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", "B", 16)
            
            # Cabeçalho
            pdf.cell(0, 10, "VirtuAr Compressores - Orcamento", 0, 1, "C")
            pdf.set_font("Arial", "", 10)
            pdf.cell(0, 6, "pecas para Compressores e Lavadoras - www.VirtuArCompressores.com.br", 0, 1, "C")
            pdf.ln(10)
            
            # Dados do Cliente
            pdf.set_font("Arial", "B", 11)
            pdf.cell(0, 6, f"Cliente: {nome_cliente}", 0, 1)
            pdf.cell(0, 6, f"Telefone: {tel_cliente}", 0, 1)
            pdf.cell(0, 6, f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}", 0, 1)
            pdf.ln(10)
            
            # Tabela de Itens
            pdf.set_fill_color(200, 220, 255)
            pdf.set_font("Arial", "B", 10)
            pdf.cell(100, 8, "Descricao do Produto", 1, 0, "L", True)
            pdf.cell(20, 8, "Qtd", 1, 0, "C", True)
            pdf.cell(35, 8, "Preco Unit.", 1, 0, "R", True)
            pdf.cell(35, 8, "Total", 1, 1, "R", True)
            
            pdf.set_font("Arial", "", 10)
            for item in st.session_state["itens_orcamento"]:
                pdf.cell(100, 8, str(item["produto"][:45]), 1, 0, "L")
                pdf.cell(20, 8, str(item["quantidade"]), 1, 0, "C")
                pdf.cell(35, 8, f"R$ {item['preco_unitario']:.2f}", 1, 0, "R")
                pdf.cell(35, 8, f"R$ {item['total']:.2f}", 1, 1, "R")
                
            pdf.ln(5)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 10, f"VALOR TOTAL: R$ {valor_total_orcamento:.2f}", 0, 1, "R")
            
            # Salva o PDF em arquivo temporário
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                pdf.output(tmp_file.name)
                with open(tmp_file.name, "rb") as pdf_file:
                    bytes_pdf = pdf_file.read()
                    
            st.download_button(
                label="📥 Baixar PDF Pronto",
                data=bytes_pdf,
                file_name=f"Orcamento_{nome_cliente.replace(' ', '_')}.pdf",
                mime="application/pdf",
                type="primary"
            )