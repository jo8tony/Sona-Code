use std::path::PathBuf;

use tauri::Manager;
use tauri_plugin_autostart::ManagerExt;

fn settings_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .config_dir()
        .map_err(|error| error.to_string())?
        .join("SonaCode/desktop-settings.json"))
}

#[tauri::command]
pub fn get_autostart(app: tauri::AppHandle) -> Result<bool, String> {
    if cfg!(debug_assertions) {
        return Err("开机自启仅在安装版桌面应用中可用".to_string());
    }
    app.autolaunch()
        .is_enabled()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn set_autostart(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    let previous = get_autostart(app.clone())?;
    let manager = app.autolaunch();
    let result = if enabled {
        manager.enable()
    } else {
        manager.disable()
    };
    result.map_err(|error| error.to_string())?;
    let persist = (|| {
        if manager.is_enabled().map_err(|error| error.to_string())? != enabled {
            return Err("系统未应用开机自启设置".to_string());
        }
        let path = settings_path(&app)?;
        let content = serde_json::json!({ "autostart": enabled }).to_string();
        let temporary = path.with_extension("json.tmp");
        std::fs::write(&temporary, content).map_err(|error| error.to_string())?;
        std::fs::rename(temporary, path).map_err(|error| error.to_string())
    })();
    if let Err(error) = persist {
        let _ = if previous {
            manager.enable()
        } else {
            manager.disable()
        };
        return Err(error);
    }
    Ok(enabled)
}

pub fn initialize(app: &tauri::AppHandle) -> Result<(), String> {
    if cfg!(debug_assertions) {
        return Ok(());
    }
    let enabled = match std::fs::read_to_string(settings_path(app)?) {
        Ok(content) => serde_json::from_str::<serde_json::Value>(&content)
            .map_err(|error| error.to_string())?
            .get("autostart")
            .and_then(|value| value.as_bool())
            .ok_or("无效的桌面设置文件")?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => true,
        Err(error) => return Err(error.to_string()),
    };
    set_autostart(app.clone(), enabled)?;
    Ok(())
}
