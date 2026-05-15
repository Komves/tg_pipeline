/*
     token
*/
function activeToken(container) {

     let result = undefined;

     let elements = $("#" + container + " > .active");
     if (elements.length) {
          result = elements[0];
     }

     return result;
}

function createToken(container, token) {

     let element = document.createElement("div");

     element.className = "token";
     element.innerHTML =
          "\
               <b>Метка: " + token.label + "</b><br/>\
               Серийный номер: " + token.serial + "<br/>\
               Хранилище: " + token.store + "<br/>\
               Модель: " + token.model + "<br/>\
               Провайдер: " + token.provider + "<br/>\
               Сертификатов: " + token.certificates.length + "<br/>\
          ";
     element.addEventListener('click', function () {
          $("#" + container + " > .token").removeClass('active');
          element.className += " active";
          updateTokenActions();
     });
     $("#" + container).append(element);

     return element;
};

function appendTokens(container) {

     clearTokens(container);

     if (typeof _crypto_0db7d9ec48754f9080a77e237238453b === "undefined" ) {
          console.warn(`token.js: crypto undefined, crypto.tokens canceled`);
          return;
     }

     let store = "";

     if      (container == "tokens_container"        ) store = "";
     else if (container == "rutoken_tokens_container") store = "Рутокен ЭЦП 2.0";
     else if (container == "jacarta_tokens_container") store = "JaCarta GOST 2.0";
     else if (container == "esmart_tokens_container" ) store = "ESMARTToken GOST";

     else return;

     let request = {};
     request.store = store;
     request.fields = {};

     _crypto_0db7d9ec48754f9080a77e237238453b.tokens( request, function (message) {

          if (message.error && message.error.length) {
               alert(message.error);
          } else {
               let result = JSON.parse(message.method.result);

               result.forEach(function (each, index) {
                    let element = createToken(container, each);
                    element.setAttribute("id", index.toString());
               });

               updateTokenActions();
          }
     });
}

function updateTokenActions() {

     const tokenFilter = document.getElementById("find_token").value;
     let active = activeToken("tokens_container");

     if (tokenFilter.length) {
          $("#tokens_container > .token").removeClass('active');
          $("#tokens_container > .token").hide();

          $("#tokens_container > .token:contains(" + tokenFilter + ")").show();

          if (active && active.innerHTML.indexOf(tokenFilter) > -1) {
               active.className += " active";
          } else {
               active = undefined;
          }
     } else {
          $("#tokens_container > .token").show();
     }

     document.getElementById("tokens_count").innerText = $("#tokens_container > .token").length;
}

function clearTokens(container) {
     $("#" + container + " > .token").remove();
     updateTokenActions();
}

function updateTokens() {
     clearTokens("tokens_container");

     appendTokens("tokens_container");
}