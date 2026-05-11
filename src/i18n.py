import os

_current_lang = "en"  # Default

def set_language(lang: str) -> None:
    global _current_lang
    if lang in TRANSLATIONS:
        _current_lang = lang

def get_language() -> str:
    return _current_lang

def t(key: str, **kwargs) -> str:
    """Get the translated string for a given key."""
    text = TRANSLATIONS.get(_current_lang, TRANSLATIONS["en"]).get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    return text

TRANSLATIONS = {
    "en": {
        # Setup / Ensure Setup
        "setup_checking_git": "Checking git installation...",
        "setup_git_installed": "✓ git installed successfully.",
        "setup_install_git": "Please install git: [link=https://git-scm.com]https://git-scm.com[/link]",
        "setup_not_git_repo_title": "⚠️  No Git Repo",
        "setup_not_git_repo": "[yellow]The current directory is not a git repository.[/yellow]",
        "setup_init_git_ask": "Initialize a git repository here?",
        "setup_git_initialized": "[green]✓ git repository initialized.[/green]",
        "setup_api_key_required_title": "🔑 API Key Required",
        "setup_api_key_required": "[bold yellow]No GROQ_API_KEY found.[/bold yellow]\nGet your free key at: [link=https://console.groq.com]https://console.groq.com[/link]",
        "setup_paste_api_key": "Paste your Groq API key",
        "setup_api_key_saved": "[green]✓ API key saved successfully.[/green]",
        "setup_git_config_missing_title": "⚠️ Missing Git Config",
        "setup_git_config_missing": "Your Git identity is not configured.\nGit needs to know who you are to create commits.",
        "setup_git_name_ask": "Enter your full name (e.g., Jane Doe)",
        "setup_git_email_ask": "Enter your email address (e.g., jane@example.com)",
        "setup_git_identity_saved": "[green]✓ Git identity saved globally.[/green]",
        "setup_lang_ask": "Select your preferred language / Selecciona tu idioma preferido",
        
        # Start command
        "start_already_running": "Watcher already running (PID {pid}).",
        "start_success": "✓ SuperGit watcher started",
        "start_failed": "Failed to start daemon.",

        # Stop command
        "stop_not_running": "Watcher is not running.",
        "stop_success": "✓ SuperGit watcher stopped.",
        
        # Status command
        "status_running": "RUNNING",
        "status_stopped": "STOPPED",
        "status_no_pending": "No pending changes.",
        
        # New command
        "new_repo_exists_title": "⚠️  Existing Repo",
        "new_repo_exists": "This directory is already a Git repository.",
        "new_overwrite_remote": "Do you want to add/overwrite the 'origin' remote anyway?",
        "new_linking_remote": "⚙ Linking remote origin to {url}...",
        "new_remote_added": "✓ Remote 'origin' added.",
        "new_remote_failed": "Failed to add remote: {error}",
        "new_staging": "⚙ Staging and committing existing files...",
        "new_commit_created": "✓ Created initial commit.",
        "new_commit_failed": "❌ Git commit failed.",
        "new_no_changes": "No changes to commit.",
        "new_renaming_pushing": "⚙ Renaming branch to 'main' and pushing to origin...",
        "new_push_success": "🎉 Project successfully uploaded to Git!\nBranch: main\nRemote: {url}",
        "new_push_failed": "❌ Git push failed.",
        "new_ai_consulting": "🤖 Consultando a la IA para diagnosticar el problema...",
        "new_ai_analyzing": "Analizando error...",
        "new_ai_proposal_title": "💡 Solución propuesta por SuperGit AI",
        
        # Commit command
        "commit_no_pending": "No pending changes to commit.",
        "commit_found_pending": "Found [cyan]{count}[/cyan] pending file change(s).",
        "commit_branch_created": "✓ Created branch [bold]{branch}[/bold]",
        "commit_branch_failed": "Could not create review branch: {error}",
        "commit_running_ai": "⚙  Running AI analysis (Map-Reduce)…",
        "commit_contacting_groq": "Contacting Groq…",
        "commit_proposal_title": "✨ Proposed Commit Message",
        "commit_what_to_do": "What would you like to do?",
        "commit_choice_merge": "[M] Merge (squash into main and delete review branch)",
        "commit_choice_edit": "[E] Edit commit message",
        "commit_choice_view": "[V] View diff of a specific file",
        "commit_choice_abort": "[A] Abort (stay on review branch)",
        "commit_choice_prompt": "Choice",
        "commit_aborted": "Aborted. You are still on branch [cyan]{branch}[/cyan]",
        "commit_merge_success": "✓ Commit merged into [cyan]{branch}[/cyan]",
        "commit_merge_deleted": "Branch [dim]{branch}[/dim] deleted.",
        
        # View command
        "view_no_pending": "No pending diff found for file: {file}",
        "view_no_diff_stored": "No diff stored for this file.",
        
        # Config command
        "config_update_api": "Update API key?",
        "config_new_api": "New Groq API key",
        "config_api_updated": "✓ API key updated.",
        "config_clear_events": "Clear all pending events (does NOT undo git changes)?",
        "config_are_you_sure": "Are you sure?",
        "config_events_cleared": "✓ Pending events cleared.",
        "config_clear_logs": "Clear all AI audit logs?",
        "config_logs_cleared": "✓ AI audit logs cleared.",
        "config_change_lang": "Change language / Cambiar idioma?",
        "config_lang_updated": "✓ Language updated.",
        
        # Logs command
        "logs_not_found": "Log entry {id} not found.",
        "logs_empty": "No audit log entries yet.",
    },
    "es": {
        # Setup / Ensure Setup
        "setup_checking_git": "Comprobando la instalación de git...",
        "setup_git_installed": "✓ git se instaló correctamente.",
        "setup_install_git": "Por favor instala git: [link=https://git-scm.com]https://git-scm.com[/link]",
        "setup_not_git_repo_title": "⚠️  No es un Repo Git",
        "setup_not_git_repo": "[yellow]El directorio actual no es un repositorio git.[/yellow]",
        "setup_init_git_ask": "¿Deseas inicializar un repositorio git aquí?",
        "setup_git_initialized": "[green]✓ repositorio git inicializado.[/green]",
        "setup_api_key_required_title": "🔑 Se requiere API Key",
        "setup_api_key_required": "[bold yellow]No se encontró GROQ_API_KEY.[/bold yellow]\nConsigue tu clave gratuita en: [link=https://console.groq.com]https://console.groq.com[/link]",
        "setup_paste_api_key": "Pega tu API key de Groq",
        "setup_api_key_saved": "[green]✓ API key guardada exitosamente.[/green]",
        "setup_git_config_missing_title": "⚠️ Falta Configuración de Git",
        "setup_git_config_missing": "Tu identidad de Git no está configurada.\nGit necesita saber quién eres para crear commits.",
        "setup_git_name_ask": "Ingresa tu nombre completo (ej., Juan Pérez)",
        "setup_git_email_ask": "Ingresa tu correo electrónico (ej., juan@ejemplo.com)",
        "setup_git_identity_saved": "[green]✓ Identidad de Git guardada globalmente.[/green]",
        "setup_lang_ask": "Selecciona tu idioma preferido / Select your preferred language",
        
        # Start command
        "start_already_running": "El guardián ya se está ejecutando (PID {pid}).",
        "start_success": "✓ Guardián SuperGit iniciado",
        "start_failed": "Fallo al iniciar el daemon.",

        # Stop command
        "stop_not_running": "El guardián no se está ejecutando.",
        "stop_success": "✓ Guardián SuperGit detenido.",
        
        # Status command
        "status_running": "EN EJECUCIÓN",
        "status_stopped": "DETENIDO",
        "status_no_pending": "No hay cambios pendientes.",
        
        # New command
        "new_repo_exists_title": "⚠️  Repo Existente",
        "new_repo_exists": "Este directorio ya es un repositorio Git.",
        "new_overwrite_remote": "¿Deseas agregar/sobrescribir el remoto 'origin' de todos modos?",
        "new_linking_remote": "⚙ Enlazando remoto origin a {url}...",
        "new_remote_added": "✓ Remoto 'origin' agregado.",
        "new_remote_failed": "Fallo al agregar el remoto: {error}",
        "new_staging": "⚙ Preparando y creando commit de los archivos existentes...",
        "new_commit_created": "✓ Commit inicial creado.",
        "new_commit_failed": "❌ Falló el commit de Git.",
        "new_no_changes": "No hay cambios para hacer commit.",
        "new_renaming_pushing": "⚙ Renombrando rama a 'main' y subiendo a origin...",
        "new_push_success": "🎉 ¡Proyecto subido a Git exitosamente!\nRama: main\nRemoto: {url}",
        "new_push_failed": "❌ Falló el push de Git.",
        "new_ai_consulting": "🤖 Consultando a la IA para diagnosticar el problema...",
        "new_ai_analyzing": "Analizando error...",
        "new_ai_proposal_title": "💡 Solución propuesta por SuperGit AI",
        
        # Commit command
        "commit_no_pending": "No hay cambios pendientes para hacer commit.",
        "commit_found_pending": "Se encontraron [cyan]{count}[/cyan] cambio(s) de archivo pendientes.",
        "commit_branch_created": "✓ Rama creada [bold]{branch}[/bold]",
        "commit_branch_failed": "No se pudo crear la rama de revisión: {error}",
        "commit_running_ai": "⚙  Ejecutando análisis de IA (Map-Reduce)…",
        "commit_contacting_groq": "Contactando a Groq…",
        "commit_proposal_title": "✨ Mensaje de Commit Propuesto",
        "commit_what_to_do": "¿Qué te gustaría hacer?",
        "commit_choice_merge": "[M] Fusionar (aplastar en main y borrar rama de revisión)",
        "commit_choice_edit": "[E] Editar mensaje de commit",
        "commit_choice_view": "[V] Ver diff de un archivo específico",
        "commit_choice_abort": "[A] Abortar (quedarse en la rama de revisión)",
        "commit_choice_prompt": "Elección",
        "commit_aborted": "Abortado. Sigues en la rama [cyan]{branch}[/cyan]",
        "commit_merge_success": "✓ Commit fusionado en [cyan]{branch}[/cyan]",
        "commit_merge_deleted": "Rama [dim]{branch}[/dim] eliminada.",
        
        # View command
        "view_no_pending": "No se encontró diff pendiente para el archivo: {file}",
        "view_no_diff_stored": "No hay diff almacenado para este archivo.",
        
        # Config command
        "config_update_api": "¿Actualizar API key?",
        "config_new_api": "Nueva API key de Groq",
        "config_api_updated": "✓ API key actualizada.",
        "config_clear_events": "¿Limpiar todos los eventos pendientes (NO deshace los cambios en git)?",
        "config_are_you_sure": "¿Estás seguro?",
        "config_events_cleared": "✓ Eventos pendientes limpiados.",
        "config_clear_logs": "¿Limpiar todos los logs de auditoría de IA?",
        "config_logs_cleared": "✓ Logs de auditoría de IA limpiados.",
        "config_change_lang": "¿Cambiar idioma / Change language?",
        "config_lang_updated": "✓ Idioma actualizado / Language updated.",
        
        # Logs command
        "logs_not_found": "Entrada de log {id} no encontrada.",
        "logs_empty": "No hay entradas de log de auditoría aún.",
    }
}
