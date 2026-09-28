use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

struct SidecarState(Mutex<Option<CommandChild>>);

#[tauri::command]
fn open_website_login(app: tauri::AppHandle, url: String) -> Result<(), String> {
    let parsed = url::Url::parse(&url).map_err(|_| "无效的网站地址".to_string())?;
    let host = parsed.host_str().ok_or("网站地址缺少域名")?;
    if parsed.scheme() != "https"
        && !(parsed.scheme() == "http" && matches!(host, "127.0.0.1" | "localhost"))
    {
        return Err("登录网站必须使用 HTTPS".to_string());
    }
    if parsed.username() != "" || parsed.password().is_some() {
        return Err("网站地址不能包含凭据".to_string());
    }
    app.shell().open(url, None).map_err(|error| error.to_string())
}

fn show_main_window(app: &tauri::AppHandle) {
    eprintln!("desktop: restoring main window");
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
    eprintln!("desktop: main window restore dispatched");
}

#[cfg(windows)]
fn setup_tray(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let show = MenuItem::with_id(app, "show", "打开 Sona Code", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &separator, &quit])?;
    let icon = app.default_window_icon().cloned().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "missing application tray icon",
        )
    })?;
    TrayIconBuilder::with_id("main")
        .icon(icon)
        .tooltip("Sona Code · 右键菜单退出")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_main_window(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn recorder_is_ready(instance_id: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], 8117));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let request =
        b"GET /__recorder/api/ping HTTP/1.1\r\nHost: 127.0.0.1:8117\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }
    response.contains(" 200 ")
        && response.contains("\"ok\":true")
        && response.contains(&format!("\"instance_id\":\"{instance_id}\""))
}

fn wait_for_recorder(instance_id: &str, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if recorder_is_ready(instance_id) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    false
}

fn stop_sidecar(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
    if let Ok(mut guard) = state.0.lock() {
        if let Some(child) = guard.take() {
            #[cfg(target_os = "macos")]
            {
                // PyInstaller one-file sidecar forks a worker; stop it before its bootloader parent.
                let _ = std::process::Command::new("pkill")
                    .args(["-TERM", "-P", &child.pid().to_string()])
                    .status();
                std::thread::sleep(Duration::from_millis(500));
            }
            #[cfg(windows)]
            {
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                let _ = std::process::Command::new("taskkill")
                    .arg("/F")
                    .arg("/T")
                    .arg("/PID")
                    .arg(child.pid().to_string())
                    .creation_flags(CREATE_NO_WINDOW)
                    .status();
            }
            let _ = child.kill();
        }
    };
}

pub fn run() {
    let builder = tauri::Builder::default();
    // Register before spawning the sidecar: a second launch restores the first window.
    #[cfg(windows)]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _, _| {
        // Windows delivers this callback through synchronous WM_COPYDATA.
        // Queue window operations from a worker so the sender can return and exit
        // before Win32 restores/focuses the first instance's window.
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            show_main_window(&app);
        });
    }));
    let app = builder
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![open_website_login])
        .on_window_event(|window, event| {
            if matches!(
                event,
                tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed
            ) {
                eprintln!("desktop: window {} event {event:?}", window.label());
            }
            #[cfg(any(target_os = "macos", windows))]
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    // Do not change Win32 visibility reentrantly inside WM_CLOSE.
                    // Let the native close callback finish before dispatching hide.
                    #[cfg(windows)]
                    {
                        let window = window.clone();
                        tauri::async_runtime::spawn(async move {
                            eprintln!("desktop: hiding main window");
                            let _ = window.hide();
                            eprintln!("desktop: main window hide dispatched");
                        });
                    }
                    #[cfg(target_os = "macos")]
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            // Fail before launching the backend if the tray cannot be created.
            #[cfg(windows)]
            setup_tray(app)?;
            let config_dir = app.path().app_config_dir()?;
            let data_dir = app.path().app_data_dir()?;
            let cache_dir = app.path().app_cache_dir()?;
            let log_dir = app.path().app_log_dir()?;
            let state_dir = data_dir.join("state");
            std::fs::create_dir_all(&config_dir)?;
            std::fs::create_dir_all(&data_dir)?;
            std::fs::create_dir_all(&cache_dir)?;
            std::fs::create_dir_all(&log_dir)?;
            std::fs::create_dir_all(&state_dir)?;

            let config_path = config_dir.join("config.json");
            let records_dir = data_dir.join("records");
            let args = [
                "--host".to_string(),
                "127.0.0.1".to_string(),
                "--port".to_string(),
                "8117".to_string(),
                "--config".to_string(),
                config_path.to_string_lossy().into_owned(),
                "--records-dir".to_string(),
                records_dir.to_string_lossy().into_owned(),
            ];

            let current_exe = std::env::current_exe()?;
            let bundled_opencode = current_exe
                .parent()
                .map(|parent| {
                    parent.join(if cfg!(windows) {
                        "opencode.exe"
                    } else {
                        "opencode"
                    })
                })
                .unwrap_or_default();
            let instance_id = format!(
                "{}-{}",
                std::process::id(),
                SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos()
            );
            let mut sidecar = app.shell().sidecar("llm-api-proxy-recorder-sidecar")?;
            for key in [
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_CACHE_HOME",
                "XDG_STATE_HOME",
            ] {
                sidecar = sidecar.env(
                    format!("LLMPR_ORIGINAL_{key}"),
                    std::env::var_os(key).unwrap_or_default(),
                );
            }
            sidecar = sidecar
                .env("PYTHONIOENCODING", "utf-8")
                .env("LLMPR_DESKTOP_INSTANCE_ID", &instance_id)
                .env("LLMPR_BUNDLED_OPENCODE", bundled_opencode)
                .env("XDG_CONFIG_HOME", &config_dir)
                .env("XDG_DATA_HOME", &data_dir)
                .env("XDG_CACHE_HOME", &cache_dir)
                .env("XDG_STATE_HOME", &state_dir);
            let (mut receiver, child) = sidecar.args(args).spawn()?;
            app.manage(SidecarState(Mutex::new(Some(child))));

            let log_path = log_dir.join("desktop.log");
            tauri::async_runtime::spawn(async move {
                let mut log_file = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(log_path)
                    .ok();
                while let Some(event) = receiver.recv().await {
                    match event {
                        CommandEvent::Stdout(bytes) => {
                            eprint!("{}", String::from_utf8_lossy(&bytes));
                            if let Some(file) = log_file.as_mut() {
                                let _ = file.write_all(&bytes);
                            }
                        }
                        CommandEvent::Stderr(bytes) => {
                            eprint!("{}", String::from_utf8_lossy(&bytes));
                            if let Some(file) = log_file.as_mut() {
                                let _ = file.write_all(&bytes);
                            }
                        }
                        _ => {}
                    }
                }
            });

            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("Sona Code")
                    // WebView2 can pump native messages during construction. Do
                    // not expose a closable window before its listeners are attached.
                    .visible(!cfg!(windows))
                    .inner_size(1280.0, 820.0)
                    .min_inner_size(900.0, 620.0)
                    .center()
                    .build()?;
            #[cfg(windows)]
            window.show()?;

            std::thread::spawn(move || {
                if wait_for_recorder(&instance_id, Duration::from_secs(60)) {
                    if let Ok(url) = url::Url::parse("http://127.0.0.1:8117/__recorder/#/workspace")
                    {
                        let _ = window.navigate(url);
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build desktop application");

    app.run(|app_handle, event| match event {
        #[cfg(windows)]
        RunEvent::ExitRequested {
            code: None, api, ..
        } => {
            // Background residency is explicit: only a requested exit code (the
            // tray Quit action) may end the Windows app and stop its backend.
            eprintln!("desktop: preventing automatic exit");
            api.prevent_exit();
        }
        RunEvent::Exit | RunEvent::ExitRequested { .. } => {
            eprintln!("desktop: exiting after {event:?}");
            stop_sidecar(app_handle);
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => {
            show_main_window(app_handle);
        }
        _ => {}
    });
}
