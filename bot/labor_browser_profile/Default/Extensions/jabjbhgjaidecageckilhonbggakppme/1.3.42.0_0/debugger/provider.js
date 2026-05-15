/*
     provider
*/
function activeProvider(container) {

     let result = undefined;

     let elements = $("#" + container + " > .active");
     if (elements.length) {
          result = elements[0];
     }

     return result;
}

function createProvider(container, provider) {

     let element = document.createElement("div");

     element.className = "provider";
     element.innerHTML =
          "\
               <b>Название: " + provider.name + "</b><br/>\
               Тип: " + provider.type + "<br/>\
               Версия: " + provider.version + "<br/>\
          ";

     if( provider.token == "true" )
     {
          element.innerHTML +=
               "\
                    Расположение: токен<br/>\
               ";
     } else {
          element.innerHTML +=
               "\
                    Расположение: oc<br/>\
               ";
     }

     element.addEventListener('click', function () {
          $("#" + container + " > .provider").removeClass('active');
          element.className += " active";
          updateProviderActions();
     });
     $("#" + container).append(element);

     return element;
};


function appendProviders(container) {

     clearProviders(container);

     let request = {};
     request.fields = {};

     if (typeof _crypto_0db7d9ec48754f9080a77e237238453b === "undefined" ) {
          console.warn(`provider.js: crypto undefined, crypto.providers canceled`);
          return;
     }
     
     _crypto_0db7d9ec48754f9080a77e237238453b.providers( request, function (message) {

          if (message.error && message.error.length) {
               alert(message.error);
          } else {
               let result = JSON.parse(message.method.result);

               result.forEach(function (each, index) {
                    let element = createProvider(container, each);
                    element.setAttribute("id", index.toString());
               });

               updateProviderActions();
          }
     });
}

function updateProviderActions() {

     const providerFilter = document.getElementById("find_provider").value;
     let active = activeProvider("providers_container");

     if (providerFilter.length) {
          $("#providers_container > .provider").removeClass('active');
          $("#providers_container > .provider").hide();

          $("#providers_container > .provider:contains(" + providerFilter + ")").show();

          if (active && active.innerHTML.indexOf(providerFilter) > -1) {
               active.className += " active";
          } else {
               active = undefined;
          }
     } else {
          $("#providers_container > .provider").show();
     }

     document.getElementById("providers_count").innerText = $("#providers_container > .provider").length;
}

function clearProviders(container) {
     $("#" + container + " > .provider").remove();
     updateProviderActions();
}

function updateProviders() {
     clearProviders("providers_container");

     appendProviders("providers_container");
}