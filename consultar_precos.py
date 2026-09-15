import sqlite3
from pathlib import Path

# Configuração de caminho do banco
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "compras_nfe.db"

def buscar_preco(termo_pesquisa):
    """Busca o histórico de preços de um produto no banco de dados."""
    
    # Conecta ao banco de dados
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Cria a consulta cruzando as tabelas de itens, notas e fornecedores
    query = """
    SELECT 
        n.data_emissao,
        f.nome AS fornecedor,
        i.descricao,
        i.quantidade,
        i.valor_unitario
    FROM itens_nota i
    JOIN notas_fiscais n ON i.chave_nfe = n.chave_nfe
    JOIN fornecedores f ON n.cnpj_fornecedor = f.cnpj
    WHERE i.descricao LIKE ?
    ORDER BY n.data_emissao DESC
    """

    # Executa a busca (os % ao redor do termo fazem a busca parcial, como um filtro "contém")
    cursor.execute(query, (f"%{termo_pesquisa}%",))
    resultados = cursor.fetchall()
    
    conn.close()

    # Mostra os resultados na tela
    print(f"\n--- Resultados da busca para: '{termo_pesquisa}' ---")
    if not resultados:
        print("Nenhum item encontrado com esse nome.")
    else:
        for linha in resultados:
            data, fornecedor, descricao, qtd, preco = linha
            
            # Formata a exibição para ficar fácil de ler
            nome_fornecedor = fornecedor[:20] + "..." if len(fornecedor) > 20 else fornecedor
            print(f"Data: {data} | R$ {preco:.2f} (unid) | Qtd: {qtd} | Fornecedor: {nome_fornecedor} | Item: {descricao}")

if __name__ == "__main__":
    # Testando com alguns itens que vieram do seu XML!
    buscar_preco("PRESSOSTATO")
    buscar_preco("VISOR DE OLEO")
    
    # Você pode pedir para o usuário digitar o que ele quer buscar:
    print("\n---------------------------------------------------")
    termo = input("Digite o nome de uma peça para buscar (ou 'sair' para fechar): ")
    if termo.lower() != 'sair':
        buscar_preco(termo.upper())