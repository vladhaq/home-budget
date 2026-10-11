function markExtensionInstalled() {
  if (!document.documentElement || document.getElementById("home-budget-biedronka-extension")) return;
  const marker = document.createElement("meta");
  marker.id = "home-budget-biedronka-extension";
  marker.name = "home-budget-biedronka-extension";
  marker.content = "1.3.1";
  document.documentElement.appendChild(marker);
}

markExtensionInstalled();
if (!document.documentElement) {
  document.addEventListener("DOMContentLoaded", markExtensionInstalled, {once: true});
}
