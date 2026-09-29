"use strict";

const path = require("node:path");

module.exports = async ({ appOutDir, packager, electronPlatformName }) => {
  if (electronPlatformName !== "win32") return;

  // signAndEditExecutable is disabled because electron-builder's winCodeSign
  // archive requires symlink privileges on some Windows build machines. Its
  // resource-editing step would otherwise set the EXE (and taskbar) icon.
  const { rcedit } = await import("rcedit");
  const executable = path.join(appOutDir, `${packager.appInfo.productFilename}.exe`);
  const icon = path.resolve(__dirname, "..", "src-tauri", "icons", "icon.ico");
  const version = packager.appInfo.version;
  for (let attempt = 1; attempt <= 8; attempt++) {
    try {
      await rcedit(executable, {
        icon,
        "file-version": version,
        "product-version": version,
        "version-string": {
          CompanyName: "Sona Code",
          FileDescription: "Sona Code",
          InternalName: "Sona Code",
          OriginalFilename: "Sona Code.exe",
          ProductName: "Sona Code",
        },
      });
      return;
    } catch (error) {
      if (attempt === 8 || !String(error).includes("Unable to commit changes")) throw error;
      // Windows can briefly hold the EXE after ASAR integrity is written.
      await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
    }
  }
};
