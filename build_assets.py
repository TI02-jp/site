"""
Script de minificação de assets CSS e JavaScript.

Este script minifica todos os assets CSS e JS do projeto,
gerando versões .min.css e .min.js otimizadas para produção.

Uso:
    python build_assets.py

Dependências:
    pip install jsmin csscompressor
"""

import os
import sys
from pathlib import Path

try:
    import jsmin
    import csscompressor
except ImportError:
    print("❌ Erro: Dependências não instaladas")
    print("\nPara instalar as dependências:")
    print("  pip install jsmin csscompressor")
    sys.exit(1)


STATIC_DIR = Path('app/static')


def minify_css(input_path, output_path):
    """
    Minifica arquivo CSS.

    Args:
        input_path: Path do arquivo CSS original
        output_path: Path para salvar versão minificada
    """
    print(f"📄 Minificando {input_path.name}...", end=" ")

    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            css = f.read()

        minified = csscompressor.compress(css)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(minified)

        # Estatísticas
        original_size = os.path.getsize(input_path) / 1024
        minified_size = os.path.getsize(output_path) / 1024
        reduction = (1 - minified_size/original_size) * 100

        print(f"✅ {original_size:.1f}KB → {minified_size:.1f}KB ({reduction:.1f}% redução)")
        return True

    except Exception as e:
        print(f"❌ Erro: {str(e)}")
        return False


def minify_js(input_path, output_path):
    """
    Minifica arquivo JavaScript.

    Args:
        input_path: Path do arquivo JS original
        output_path: Path para salvar versão minificada
    """
    print(f"📄 Minificando {input_path.name}...", end=" ")

    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            js = f.read()

        minified = jsmin.jsmin(js)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(minified)

        # Estatísticas
        original_size = os.path.getsize(input_path) / 1024
        minified_size = os.path.getsize(output_path) / 1024
        reduction = (1 - minified_size/original_size) * 100

        print(f"✅ {original_size:.1f}KB → {minified_size:.1f}KB ({reduction:.1f}% redução)")
        return True

    except Exception as e:
        print(f"❌ Erro: {str(e)}")
        return False


def build_assets():
    """Constrói todos os assets minificados."""
    print("=" * 60)
    print("🚀 Iniciando build de assets")
    print("=" * 60)
    print()

    if not STATIC_DIR.exists():
        print(f"❌ Erro: Diretório {STATIC_DIR} não encontrado")
        return False

    total_original = 0
    total_minified = 0
    success_count = 0
    error_count = 0

    # CSS files
    print("📦 Minificando arquivos CSS...")
    print("-" * 60)

    css_files = [
        ('styles.css', 'styles.min.css'),
        ('tasks.css', 'tasks.min.css'),
        ('mobile.css', 'mobile.min.css'),
        ('dark-theme.css', 'dark-theme.min.css'),
    ]

    for src, dest in css_files:
        src_path = STATIC_DIR / src
        dest_path = STATIC_DIR / dest

        if src_path.exists():
            if minify_css(src_path, dest_path):
                total_original += os.path.getsize(src_path)
                total_minified += os.path.getsize(dest_path)
                success_count += 1
            else:
                error_count += 1
        else:
            print(f"⚠️  Arquivo {src} não encontrado, pulando...")

    print()

    # JavaScript files
    print("📦 Minificando arquivos JavaScript...")
    print("-" * 60)

    js_dir = STATIC_DIR / 'javascript'

    js_files = [
        ('tasks.js', 'tasks.min.js'),
        ('notifications.js', 'notifications.min.js'),
        ('realtime.js', 'realtime.min.js'),
        ('modal_cleanup.js', 'modal_cleanup.min.js'),
        ('contatos.js', 'contatos.min.js'),
        ('paste_images.js', 'paste_images.min.js'),
        ('mensagens.js', 'mensagens.min.js'),
    ]

    for src, dest in js_files:
        src_path = js_dir / src
        dest_path = js_dir / dest

        if src_path.exists():
            if minify_js(src_path, dest_path):
                total_original += os.path.getsize(src_path)
                total_minified += os.path.getsize(dest_path)
                success_count += 1
            else:
                error_count += 1
        else:
            print(f"⚠️  Arquivo {src} não encontrado, pulando...")

    print()
    print("=" * 60)
    print("📊 Resumo do Build")
    print("=" * 60)
    print(f"✅ Arquivos minificados com sucesso: {success_count}")
    if error_count > 0:
        print(f"❌ Arquivos com erro: {error_count}")

    total_original_kb = total_original / 1024
    total_minified_kb = total_minified / 1024
    total_reduction = (1 - total_minified_kb/total_original_kb) * 100 if total_original_kb > 0 else 0

    print(f"📦 Tamanho total original: {total_original_kb:.1f} KB")
    print(f"📦 Tamanho total minificado: {total_minified_kb:.1f} KB")
    print(f"🎯 Redução total: {total_reduction:.1f}%")

    # Estimativa com gzip
    estimated_gzip = total_minified_kb * 0.25  # ~75% de compressão adicional
    print(f"📦 Estimativa com gzip: ~{estimated_gzip:.1f} KB")

    print()
    print("=" * 60)
    print("✨ Build concluído!")
    print("=" * 60)
    print()
    print("Próximos passos:")
    print("1. Adicione USE_MINIFIED_ASSETS=true ao arquivo .env")
    print("2. Atualize base.html para usar assets minificados")
    print("3. Reinicie a aplicação")
    print()

    return error_count == 0


if __name__ == '__main__':
    success = build_assets()
    sys.exit(0 if success else 1)
