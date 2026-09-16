import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path
import tempfile
import html
from fpdf import FPDF

from criar_banco import (
    DB_PATH,
    PASTA_XMLS,
    processar_xml,
    salvar_no_banco,
    importar_todos_xmls,
    salvar_xml_upload,
)

st.set_page_config(page_title="VirtuAr - Gestão de Compras e Orçamentos", layout="wide")

BASE_DIR = Path(__file__).resolve().parent

def get_connection():
    return sqlite3.connect(DB_PATH)

# ================= FUNÇÃO PARA LIMPAR TEXTOS DO XML =================
def limpar_nome_peca(nome):
    if isinstance(nome, str):
        nome = html.unescape(nome)
        nome = nome.replace("&#168;", '"').replace("¨", '"').replace("&amp;", "&")
    return nome

def obter_frete_por_peso(peso, tabela):
    for peso_maximo, valor in tabela:
        if peso <= peso_maximo:
            return valor
    return tabela[-1][1]

FRETE_NORMAL = [
    (0.3, 6.55), (0.5, 6.65), (1.0, 6.75), (1.5, 6.85),
    (2.0, 6.95), (3.0, 7.95), (4.0, 8.15), (5.0, 8.35),
    (6.0, 8.55), (7.0, 8.75), (8.0, 8.95), (9.0, 9.15),
    (11.0, 9.55), (13.0, 9.95), (15.0, 10.15), (17.0, 10.35),
    (20.0, 10.55), (25.0, 10.95), (30.0, 11.15), (40.0, 11.35),
    (50.0, 11.55), (60.0, 11.75), (70.0, 11.95), (80.0, 12.15),
    (90.0, 12.35), (100.0, 12.55), (float("inf"), 12.75),
]

SUPER_FRETE = [
    (0.3, 12.35), (0.5, 13.25), (1.0, 13.85), (1.5, 14.15),
    (2.0, 14.45), (3.0, 15.75), (4.0, 17.05), (5.0, 18.45),
    (6.0, 25.45), (7.0, 27.05), (8.0, 28.85), (9.0, 29.65),
    (11.0, 41.25), (13.0, 42.15), (15.0, 45.05), (17.0, 48.55),
    (20.0, 54.75), (25.0, 64.05), (30.0, 65.95), (40.0, 67.75),
    (50.0, 70.25), (60.0, 74.95), (70.0, 80.25), (80.0, 83.95),
    (90.0, 93.25), (100.0, 106.55), (125.0, 119.25), (150.0, 126.55),
    (151.0, 166.15),
]

# ================= MENU LATERAL COM LOGO E SESSÃO =================
logo_path = BASE_DIR / "logo.png"
if logo_path.exists():
    st.sidebar.image(str(logo_path), use_container_width=True)

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
    
    try:
        conn = get_connection()
        hoje = datetime.now()
        mes_atual = hoje.strftime("%m")
        ano_atual = hoje.strftime("%Y")

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), SUM(valor_total) FROM notas_fiscais WHERE strftime('%Y-%m', data_emissao) = ?", (f"{ano_atual}-{mes_atual}",))
        res_mes = cursor.fetchone()
        qtd_notas_mes = res_mes[0] if res_mes and res_mes[0] else 0
        total_gasto_mes = res_mes[1] if res_mes and res_mes[1] else 0.0
        
        cursor.execute("SELECT COUNT(*), SUM(valor_total) FROM notas_fiscais")
        res_geral = cursor.fetchone()
        qtd_notas_total = res_geral[0] if res_geral and res_geral[0] else 0
        total_gasto_geral = res_geral[1] if res_geral and res_geral[1] else 0.0

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
    except Exception as e:
        st.error(f"Erro ao carregar o dashboard: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# ================= TELA 2: CONSULTAS =================
elif menu == "🔍 Consultas e Filtros":
    st.title("Consulta de Peças e Preços")
    try:
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
    except Exception as e:
        st.error(f"Erro ao consultar o banco de dados: {e}")
        df = pd.DataFrame()
    finally:
        if 'conn' in locals():
            conn.close()

    st.write(f"**Resultados encontrados:** {len(df)}")
    if not df.empty:
        df["Produto"] = df["Produto"].apply(limpar_nome_peca)
        if termo_pesquisa and "Preço Un. (R$)" in df.columns:
            menor_preco = df["Preço Un. (R$)"].min()
            st.success(f"💡 O menor preço encontrado nesta busca foi **R$ {menor_preco:.2f}**")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.warning("Nenhum registro encontrado.")

# ================= TELA 3: UPLOAD =================
elif menu == "📤 Upload de XML":
    st.title("Importar Novas Notas Fiscais")
    st.write("Arraste os arquivos XML para adicionar compras ao banco de dados e salvá-los permanentemente.")

    PASTA_XMLS.mkdir(parents=True, exist_ok=True)

    arquivos = st.file_uploader(
        "Solte os arquivos XML aqui",
        type=["xml"],
        accept_multiple_files=True
    )

    if arquivos:
        if st.button("Processar e Salvar"):
            sucessos = 0
            ja_existentes = 0
            erros = 0

            barra = st.progress(0)

            for idx, arquivo in enumerate(arquivos):
                try:
                    caminho_xml = salvar_xml_upload(arquivo)
                    dados = processar_xml(caminho_xml)

                    if dados:
                        salvar_no_banco(dados)
                        sucessos += 1
                    else:
                        ja_existentes += 1

                except Exception as e:
                    erros += 1
                    st.error(f"Erro na nota {arquivo.name}: {e}")

                barra.progress((idx + 1) / len(arquivos))

            st.success(f"✅ {sucessos} nota(s) processada(s) e salva(s) com sucesso!")
            if ja_existentes:
                st.info(f"📁 {ja_existentes} arquivo(s) já existiam ou foram verificados.")
            if erros:
                st.warning(f"⚠️ {erros} arquivo(s) apresentou(aram) erro.")

    st.markdown("---")
    st.subheader("📁 Sincronizar Pasta Local de XMLs")
    st.write("Se você jogou arquivos XML diretamente na pasta do Windows (`xmls`), clique abaixo para atualizar o sistema:")
    
    if st.button("🔄 Sincronizar Todos os XMLs da Pasta"):
        try:
            sucessos_pasta, erros_pasta = importar_todos_xmls()
            st.success(f"✅ Sincronização concluída! {sucessos_pasta} nota(s) importada(s) da pasta com sucesso.")
            if erros_pasta > 0:
                st.warning(f"⚠️ {erros_pasta} arquivo(s) na pasta apresentou(aram) erro ao ser processado.")
        except Exception as e:
            st.error(f"Erro ao sincronizar pasta: {e}")

# ================= TELA 4: CALCULADORA =================
elif menu == "💰 Calculadora de Preços":
    st.title("Calculadora de Preços (Espelho da Planilha)")
    st.write("Cálculo exato de Markup Reverso considerando comissões, impostos e custos de frete por peso.")

    custo_sugerido = 0.0
    lista_produtos = ["Digitar valor manualmente..."]
    mapa_prods = {}

    try:
        conn = get_connection()
        df_produtos = pd.read_sql_query("SELECT DISTINCT descricao FROM itens_nota ORDER BY descricao", conn)
        if not df_produtos.empty:
            df_produtos["descricao_tela"] = df_produtos["descricao"].apply(limpar_nome_peca)
            mapa_prods = dict(zip(df_produtos["descricao_tela"], df_produtos["descricao"]))
            lista_produtos = ["Digitar valor manualmente..."] + list(mapa_prods.keys())
    except Exception:
        pass
    finally:
        if 'conn' in locals():
            conn.close()
    
    st.subheader("1. Produto e Custo")
    produto_selecionado = st.selectbox("Selecione a peça para puxar o custo:", lista_produtos)
    
    if produto_selecionado != "Digitar valor manualmente...":
        prod_db = mapa_prods[produto_selecionado]
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT i.valor_unitario 
                FROM itens_nota i
                JOIN notas_fiscais n ON i.chave_nfe = n.chave_nfe
                WHERE i.descricao = ?
                ORDER BY n.data_emissao DESC LIMIT 1
            """, (prod_db,))
            resultado = cursor.fetchone()
            if resultado and resultado[0]:
                custo_sugerido = resultado[0]
                st.info(f"💡 Último custo de compra (mais atual): **R$ {custo_sugerido:.2f}**")
        except Exception:
            pass
        finally:
            if 'conn' in locals():
                conn.close()

    col_c1, col_c2 = st.columns(2)
    custo_produto = col_c1.number_input("Custo da Peça (R$)", min_value=0.0, value=float(custo_sugerido), step=1.0)
    peso_produto = col_c2.number_input("Peso (kg) - *Base para Tabela de Frete*", min_value=0.0, value=0.5, step=0.1)

    st.subheader("2. Taxas e Parâmetros (%)")
    col_t1, col_t2, col_t3 = st.columns(3)
    tipo_anuncio = col_t1.selectbox("Tipo de Anúncio", ["PREMIUM", "CLASSICO", "SHOPPE", "LOJA INTEGRADA"])
    
    if tipo_anuncio == "PREMIUM":
        comissao_padrao, base_padrao, flex_padrao = 21.11, None, 12.99
    elif tipo_anuncio == "CLASSICO":
        comissao_padrao, base_padrao, flex_padrao = 16.11, None, 12.99
    elif tipo_anuncio == "SHOPPE":
        comissao_padrao, base_padrao, flex_padrao = 23.50, 5.00, 10.99
    else:
        comissao_padrao, base_padrao, flex_padrao = 21.39, 0.50, 12.99

    frete_tabela_calculado = obter_frete_por_peso(peso_produto, FRETE_NORMAL)
    super_frete_calculado = obter_frete_por_peso(peso_produto, SUPER_FRETE)
    frete_sem_frete_gratis = frete_tabela_calculado if base_padrao is None else base_padrao
    frete_com_frete_gratis = super_frete_calculado if base_padrao is None else base_padrao

    taxa_comissao = col_t1.number_input("Taxa de Comissão (%)", min_value=0.0, value=float(comissao_padrao), step=0.01)
    imposto_governo = col_t2.number_input("Imposto Governo (%)", min_value=0.0, value=10.0, step=0.1)
    margem_liquida = col_t3.number_input("Margem Líquida Desejada (%)", min_value=0.0, value=15.0, step=1.0)

    st.subheader("3. Custos de Frete (R$)")
    col_f1, col_f2, col_f3 = st.columns(3)
    custo_fixo_sem_frete = col_f1.number_input("Frete normal / custo base (R$)", min_value=0.0, value=float(frete_sem_frete_gratis), step=0.5)
    custo_frete_gratis = col_f2.number_input("Super Frete / custo base (R$)", min_value=0.0, value=float(frete_com_frete_gratis), step=0.5)
    custo_flex = col_f3.number_input("Custo Flex (R$)", min_value=0.0, value=float(flex_padrao), step=0.5)

    if st.button("Calcular Preços Exatos", type="primary"):
        soma_percentuais = (taxa_comissao + imposto_governo + margem_liquida) / 100
        if soma_percentuais >= 1:
            st.error("Erro: A soma das porcentagens ultrapassa ou iguala 100%. Verifique os valores.")
        elif custo_produto == 0:
            st.warning("Insira um custo válido.")
        else:
            divisor = 1 - soma_percentuais
            
            preco_sem_frete = (custo_produto + custo_fixo_sem_frete) / divisor
            preco_com_frete = (custo_produto + custo_frete_gratis) / divisor
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
    st.write("Gerencie clientes, preencha os dados e gere o PDF com o layout Padrão VirtuAr.")

    # ------------------ INÍCIO DA GESTÃO DE CLIENTES ------------------
    st.subheader("1. Seleção de Cliente Cadastrado ou Novo")
    
    try:
        conn = get_connection()
        # Lê todos os clientes do banco
        df_cli_completo = pd.read_sql_query("SELECT id, cnpj_cpf, razao_social, telefone, endereco, cidade_uf, cep FROM clientes ORDER BY razao_social", conn)
    except Exception:
        df_cli_completo = pd.DataFrame(columns=["id", "cnpj_cpf", "razao_social", "telefone", "endereco", "cidade_uf", "cep"])
    finally:
        if 'conn' in locals():
            conn.close()

    lista_nomes_clientes = ["+ Cadastrar / Usar Novo Cliente"] + (df_cli_completo["razao_social"].tolist() if not df_cli_completo.empty else [])
    
    col_sel_cli1, col_sel_cli2 = st.columns([2, 1])
    cliente_escolhido = col_sel_cli1.selectbox("🏢 Buscar Cliente no Banco", lista_nomes_clientes)

    # Variáveis padrão para novos clientes
    v_nome, v_cnpj, v_tel, v_end, v_cid, v_cep = "Cliente Balcão", "00.000.000/0001-00", "(31) 9____-____", "Rua Principal, 100", "Contagem - MG", "32000-000"

    # Se um cliente do banco for selecionado, substitui as variáveis com os dados dele
    if cliente_escolhido != "+ Cadastrar / Usar Novo Cliente" and not df_cli_completo.empty:
        dados_cli = df_cli_completo[df_cli_completo["razao_social"] == cliente_escolhido].iloc[0]
        v_nome = dados_cli["razao_social"] if pd.notna(dados_cli["razao_social"]) else ""
        v_cnpj = dados_cli["cnpj_cpf"] if pd.notna(dados_cli["cnpj_cpf"]) else ""
        v_tel = dados_cli["telefone"] if pd.notna(dados_cli["telefone"]) else ""
        v_end = dados_cli["endereco"] if pd.notna(dados_cli["endereco"]) else ""
        v_cid = dados_cli["cidade_uf"] if pd.notna(dados_cli["cidade_uf"]) else ""
        v_cep = dados_cli["cep"] if pd.notna(dados_cli["cep"]) else ""

    st.subheader("2. Dados da Cotação e Ordem de Compra")
    col_num1, col_num2 = st.columns(2)
    num_cotacao = col_num1.text_input("🔢 Número da Cotação / Orçamento", f"COT-{datetime.now().strftime('%Y%m%d')}-01")
    num_oc = col_num2.text_input("📋 N° da Ordem de Compra (Cliente - Opcional)", "")

    st.subheader("3. Dados do Cliente e Logística")
    col_c1, col_c2, col_c3 = st.columns(3)
    nome_cliente = col_c1.text_input("👤 Nome / Razão Social", v_nome)
    cnpj_cliente = col_c2.text_input("📄 CPF / CNPJ", v_cnpj)
    tel_cliente = col_c3.text_input("📞 Telefone / WhatsApp", v_tel)

    col_e1, col_e2, col_e3 = st.columns(3)
    end_cliente = col_e1.text_input("🏠 Endereço", v_end)
    cid_cliente = col_e2.text_input("🏙️ Cidade / UF", v_cid)
    cep_cliente = col_e3.text_input("📮 CEP", v_cep)

    # Botão para salvar esse cliente (novo ou atualizado) no banco de dados automaticamente
    salvar_cliente_novo = st.checkbox("💾 Salvar ou atualizar este cliente na base de dados para futuras cotações", value=True)
    # ------------------ FIM DA GESTÃO DE CLIENTES ------------------

    opcoes_pagamento = [
        "À vista (Dinheiro/PIX)",
        "À vista (Cartão de Débito)",
        "À vista (Cartão de Crédito)",
        "Boleto Bancário",
        "30 Dias",
        "Parcelado (3x)",
        "7 Dias",
        "21/35 Dias",
        "28/56 Dias",
        "30/60/90 Dias",
        "30/60/90/120 Dias"
    ]

    col_l1, col_l2, col_l3, col_l4 = st.columns(4)
    transportadora = col_l1.text_input("🚚 Transportadora", "Correios / Retirada")
    peso_total_orc = col_l2.text_input("⚖️ Peso Total", "1 kg")
    cond_pagamento = col_l3.selectbox("💳 Cond. Pagamento", opcoes_pagamento)
    vendedor = col_l4.text_input("👔 Vendedor Responsável", "VirtuAr Compressores")

    col_o1, col_o2 = st.columns(2)
    prazo_entrega = col_o1.text_input("⏳ Prazo de Entrega", "Imediato / 2 dias úteis")
    validade_proposta = col_o2.text_input("📅 Validade da Proposta", "7 Dias")
    observacoes = st.text_area("📝 Observações da Cotação (Garantia, Sinal, Avisos, etc.)", "Garantia de 3 meses contra defeitos de fabricação.\nEntrega mediante confirmação de pagamento.")

    st.divider()
    st.subheader("4. Adicionar Produtos ao Orçamento")
    
    lista_prods = []
    mapa_prods = {}
    try:
        conn = get_connection()
        df_produtos = pd.read_sql_query("SELECT DISTINCT descricao FROM itens_nota ORDER BY descricao", conn)
        if not df_produtos.empty:
            df_produtos["descricao_tela"] = df_produtos["descricao"].apply(limpar_nome_peca)
            mapa_prods = dict(zip(df_produtos["descricao_tela"], df_produtos["descricao"]))
            lista_prods = list(mapa_prods.keys())
    except Exception:
        pass
    finally:
        if 'conn' in locals():
            conn.close()

    if "itens_orcamento" not in st.session_state:
        st.session_state["itens_orcamento"] = []

    if lista_prods:
        col_i1, col_i2, col_i3 = st.columns([3, 1, 1])
        prod_escolhido = col_i1.selectbox("Selecione a Peça no Histórico", lista_prods)
        
        custo_bd = 0.0
        if prod_escolhido and prod_escolhido in mapa_prods:
            prod_db = mapa_prods[prod_escolhido]
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT i.valor_unitario 
                    FROM itens_nota i
                    JOIN notas_fiscais n ON i.chave_nfe = n.chave_nfe
                    WHERE i.descricao = ?
                    ORDER BY n.data_emissao DESC LIMIT 1
                """, (prod_db,))
                res = cursor.fetchone()
                if res and res[0]:
                    custo_bd = res[0]
            except Exception:
                pass
            finally:
                if 'conn' in locals():
                    conn.close()

        qtd_item = col_i2.number_input("Quantidade", min_value=1, value=1)
        preco_item = col_i3.number_input("Preço Unit. Sugerido (R$)", min_value=0.0, value=float(custo_bd), step=1.0)
        
        if st.button("➕ Adicionar Item na Cotação"):
            st.session_state["itens_orcamento"].append({
                "produto": prod_escolhido,
                "quantidade": qtd_item,
                "preco_unitario": preco_item,
                "total": qtd_item * preco_item
            })
            st.rerun()

    if st.session_state["itens_orcamento"]:
        st.write("#### 🛒 Itens Selecionados na Cotação")
        df_carrinho = pd.DataFrame(st.session_state["itens_orcamento"])
        st.dataframe(df_carrinho, use_container_width=True, hide_index=True)
        
        col_f1, col_f2 = st.columns(2)
        valor_frete = col_f1.number_input("📦 Valor Total do Frete (R$)", min_value=0.0, value=0.0, step=5.0)
        
        total_produtos = df_carrinho["total"].sum()
        total_geral = total_produtos + valor_frete
        
        st.markdown(f"#### 📦 Valor dos Produtos: R$ {total_produtos:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        st.markdown(f"### 💰 **VALOR TOTAL DA COTAÇÃO: R$ {total_geral:,.2f}**".replace(",", "X").replace(".", ",").replace("X", "."))

        col_b1, col_b2 = st.columns(2)
        if col_b1.button("🗑️ Limpar Lista de Itens"):
            st.session_state["itens_orcamento"] = []
            st.rerun()

        if col_b2.button("📥 Gerar PDF Oficial (Padrão VirtuAr)", type="primary"):
            
            # --- SALVAR CLIENTE NO BANCO ---
            if salvar_cliente_novo and nome_cliente and nome_cliente != "Cliente Balcão":
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    # Salva ou atualiza os dados do cliente usando o CNPJ/CPF como chave única
                    cursor.execute("""
                        INSERT INTO clientes (cnpj_cpf, razao_social, telefone, endereco, cidade_uf, cep)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(cnpj_cpf) DO UPDATE SET
                            razao_social = excluded.razao_social,
                            telefone = excluded.telefone,
                            endereco = excluded.endereco,
                            cidade_uf = excluded.cidade_uf,
                            cep = excluded.cep
                    """, (cnpj_cliente, nome_cliente, tel_cliente, end_cliente, cid_cliente, cep_cliente))
                    conn.commit()
                except Exception as e:
                    print(f"Erro ao salvar cliente: {e}")
                finally:
                    if 'conn' in locals():
                        conn.close()

            # --- GERAÇÃO DO PDF ---
            pdf = FPDF()
            pdf.add_page()
            
            if logo_path.exists():
                pdf.image(str(logo_path), x=10, y=3, w=40)
                
            pdf.set_xy(90, 7)
            pdf.set_font("Arial", "B", 14)
            pdf.set_text_color(20, 50, 120)
            pdf.cell(110, 6, "VIRTUAR COMPRESSORES", 0, 1, "R")
            
            pdf.set_x(90)
            pdf.set_font("Arial", "", 8)
            pdf.set_text_color(80, 80, 80)
            pdf.cell(110, 4, "Rua Manoel Teixeira de Camargos, n 90 - Loja 1 - Bairro Gloria - Contagem/MG", 0, 1, "R")
            
            pdf.set_x(90)
            pdf.cell(110, 4, "Tel: (31) 2888-0533 / (31) 98288-1653", 0, 1, "R")
            
            pdf.set_x(90)
            pdf.set_font("Arial", "B", 8)
            pdf.set_text_color(20, 90, 180)
            pdf.cell(110, 4, "www.VirtuArCompressores.com.br", 0, 1, "R")
            
            pdf.set_xy(50, 27)
            pdf.set_font("Arial", "B", 12)
            pdf.set_text_color(0, 0, 0)
            texto_oc = f" | ORDEM DE COMPRA (OC): {num_oc}" if num_oc else ""
            pdf.cell(
                150,
                7,
                f"COTACAO DE VENDA / ORCAMENTO: {num_cotacao}{texto_oc}",
                0,
                1,
                "C"
            )
            
            pdf.set_draw_color(180, 180, 180)
            pdf.set_line_width(0.3)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(2.5)
            
            # --- DADOS DO CLIENTE ---
            pdf.set_font("Arial", "B", 8.5)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(100, 4.5, f"Cliente: {nome_cliente}", 0, 0)
            pdf.cell(90, 4.5, f"Data do Documento: {datetime.now().strftime('%d/%m/%Y')}", 0, 1)
            
            pdf.set_font("Arial", "", 8.5)
            pdf.cell(100, 4.5, f"Endereco: {end_cliente} - {cid_cliente} - CEP: {cep_cliente}", 0, 0)
            pdf.cell(90, 4.5, f"Validade da Proposta: {validade_proposta}", 0, 1)
            
            pdf.cell(100, 4.5, f"CPF/CNPJ: {cnpj_cliente}", 0, 0)
            pdf.cell(90, 4.5, f"Prazo de Entrega: {prazo_entrega}", 0, 1)
            
            pdf.cell(100, 4.5, f"Telefone: {tel_cliente}", 0, 0)
            pdf.cell(90, 4.5, f"Transportadora: {transportadora} (Peso: {peso_total_orc})", 0, 1)
            
            pdf.cell(100, 4.5, f"Vendedor: {vendedor}", 0, 0)
            pdf.cell(90, 4.5, f"Cond. Pagamento: {cond_pagamento}", 0, 1)
            
            pdf.ln(2.5)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(2.5)
            
            # --- TABELA DE PRODUTOS ---
            pdf.set_fill_color(23, 100, 175) 
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Arial", "B", 8)
            pdf.cell(110, 6, "  Descricao do Item", 1, 0, "L", True)
            pdf.cell(15, 6, "Qtd", 1, 0, "C", True)
            pdf.cell(30, 6, "Preco Unit.", 1, 0, "R", True)
            pdf.cell(35, 6, "Total", 1, 1, "R", True)
            
            pdf.set_font("Arial", "", 8)
            pdf.set_text_color(0, 0, 0)
            pdf.set_fill_color(255, 255, 255)
            
            for item in st.session_state["itens_orcamento"]:
                pdf.cell(110, 6, f"  {str(item['produto'][:60])}", 1, 0, "L", True)
                pdf.cell(15, 6, str(item["quantidade"]), 1, 0, "C", True)
                pdf.cell(30, 6, f"R$ {item['preco_unitario']:.2f}", 1, 0, "R", True)
                pdf.cell(35, 6, f"R$ {item['total']:.2f}", 1, 1, "R", True)
            
            if valor_frete > 0:
                pdf.set_font("Arial", "B", 8)
                pdf.cell(125, 6, "  FRETE / TRANSPORTE", 1, 0, "L", True)
                pdf.cell(30, 6, "-", 1, 0, "C", True)
                pdf.cell(35, 6, f"R$ {valor_frete:.2f}", 1, 1, "R", True)

            pdf.ln(5)
            
            # --- OBSERVAÇÕES E TOTAIS NO RODAPÉ DA TABELA ---
            y_totais = pdf.get_y()
            
            pdf.set_xy(10, y_totais)
            pdf.set_font("Arial", "B", 8.5)
            pdf.cell(90, 5, "OBSERVACOES:", 0, 1, "L")
            pdf.set_font("Arial", "", 8)
            pdf.multi_cell(90, 4, txt=observacoes)
            
            pdf.set_xy(110, y_totais)
            pdf.set_font("Arial", "B", 9)
            pdf.set_text_color(0, 0, 0)
            
            pdf.cell(45, 6, "TOTAL EM PRODUTOS:", 0, 0, "R")
            pdf.cell(35, 6, f"R$ {total_produtos:,.2f}", 0, 1, "R")
            
            if valor_frete > 0:
                pdf.set_x(110)
                pdf.cell(45, 6, "VALOR DO FRETE:", 0, 0, "R")
                pdf.cell(35, 6, f"R$ {valor_frete:,.2f}", 0, 1, "R")
                
            pdf.set_x(110)
            pdf.cell(45, 6, "VALOR TOTAL GERAL:", 0, 0, "R")
            pdf.cell(35, 6, f"R$ {total_geral:,.2f}", 0, 1, "R")
            
            # Salva PDF Temporário
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                pdf.output(tmp_file.name)
                with open(tmp_file.name, "rb") as f_pdf:
                    bytes_pdf = f_pdf.read()
                    
            st.download_button(
                label="📥 Gerar PDF Oficial (Padrão VirtuAr)",
                data=bytes_pdf,
                file_name=f"Orcamento_{num_cotacao}.pdf",
                mime="application/pdf",
                type="primary"
            )