/*
     module
*/
function activeModule(container) {

     let result = undefined;

     let elements = $("#" + container + " > .active");
     if (elements.length) {
          result = elements[0];
     }

     return result;
}

function createModule(container, module) {

     let element = document.createElement("div");
     let text = "";

     text =
          "\
               <b>Тип: " + module.format.type + "</b><br/>\
               Описание: " + module.format.description + "<br/>\
               Формат: .Тип: " + module.format.type + " .Версия: " + module.format.version + " .Категория: " + module.format.category + "<br/>\
          ";

     module.dependencies.forEach(function (each, index) {
          text +=
               "\
               Зависимость:  .Тип: " + each.format.type + " .Версия: " + each.format.version + "<br/>\
          ";
     });

     module.methods.forEach(function (each, index) {
          text +=
               "\
               Метод: <br/>\
               .Тип: " + each.type + "<br/>\
               .Описание: " + each.description + "<br/>\
               .Данные: " + each.data + "<br/>\
               .Результат: " + each.result + "<br/>\
          ";
     });

     element.className = "module";
     element.innerHTML = text;
     element.title = JSON.stringify(module);

     element.addEventListener('click', function () {
          $("#" + container + " > .module").removeClass('active');
          element.className += " active";
          updateModuleActions();
     });
     $("#" + container).append(element);

     return element;
};

function appendModules(container) {

     clearModules(container);

     if (typeof _system_0db7d9ec48754f9080a77e237238453b === "undefined" ) {
          console.warn(`module.js: system undefined, system.config canceled`);
          return;
     }

     _system_0db7d9ec48754f9080a77e237238453b.config( function (message) {

          if (message.error && message.error.length) {
               notifier.notify(message.error);
          } else {
               let result = JSON.parse(message.method.result);

               result.forEach(function (each, index) {
                    let element = createModule(container, each);
                    element.setAttribute("id", index.toString());
               });

               updateModuleActions();
          }
     });
}

function updateModuleActions() {

     const moduleFilter = document.getElementById("find_module").value;
     let active = activeModule("modules_container");

     if (moduleFilter.length) {
          $("#modules_container > .module").removeClass('active');
          $("#modules_container > .module").hide();

          $("#modules_container > .module:contains(" + moduleFilter + ")").show();

          if (active && active.innerHTML.indexOf(moduleFilter) > -1) {
               active.className += " active";
          } else {
               active = undefined;
          }
     } else {
          $("#modules_container > .module").show();
     }

     document.getElementById("modules_count").innerText = $("#modules_container > .module").length;
}

function clearModules(container) {
     $("#" + container + " > .module").remove();
     updateModuleActions();
}

function updateModules() {
     clearModules("modules_container");

     appendModules("modules_container");
}