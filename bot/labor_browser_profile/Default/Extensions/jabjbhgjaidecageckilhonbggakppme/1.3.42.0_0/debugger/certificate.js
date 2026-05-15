/*
     certificate
*/
function activeCertificate(container) {

     let result = undefined;

     let elements = $("#" + container + " > .active");
     if (elements.length) {
          result = elements[0];
     }

     return result;
}

function createCertificate(container, certificate) {

     let element = document.createElement("div");

     let now = new Date();
     let notBefore = dateFromTimestamp(certificate.notBefore);
     let notAfter = dateFromTimestamp(certificate.notAfter);
     let expired = new Date(certificate.notAfter * 1000).getTime() < new Date().getTime();

     element.className = "certificate";
     element.title = certificate.raw;
     element.dataset.certificate = JSON.stringify(certificate);

     element.innerHTML =
          "\
               <b>Версия:</b> " + certificate.version + "<br/>\
               <b>Серийный номер:</b> " + certificate.serialNumber + "<br/>\
               <b>Алгоритм подписи:</b> " + certificate.signatureAlgorithm + "<br/>\
               <b>Издатель:</b> " + certificate.issuer + "<br/>\
               <b>Действителен c:</b> " + notBefore + "<br/>\
               <b>Действителен по:</b> " + (expired ? ("<span style='color:red'>" + notAfter + "</span>") : notAfter) + "<br/>\
               <b>Субъект:</b> " + certificate.subject + "<br/>\
               <b>Контейнер:</b>  " + certificate.container + "<br/>\
               <b>Провайдер:</b><span class='crypto'> " + certificate.provider + "</span>\
          ";

     element.addEventListener('click', function () {
          $("#" + container + " > .certificate").removeClass('active');
          element.className += " active";
          updateCertificateActions();
     });
     $("#" + container).append(element);

     return element;
};

function appendCertificates(container) {

     clearCertificates(container);

     if (typeof _crypto_0db7d9ec48754f9080a77e237238453b === "undefined" ) {
          console.warn(`certificate.js: crypto undefined, crypto.certificates canceled`);
          return;
     }

     let store = "";

     if      (container == "my_certificates_container"                   ) store = "My";
     else if (container == "root_certificates_container"                 ) store = "Root";
     else if (container == "address_book_certificates_container"         ) store = "AddressBook";
     else if (container == "auth_root_certificates_container"            ) store = "AuthRoot";
     else if (container == "certificate_authority_certificates_container") store = "CertificateAuthority";
     else if (container == "disallowed_certificates_container"           ) store = "Disallowed";
     else if (container == "trusted_people_certificates_container"       ) store = "TrustedPeople";
     else if (container == "trusted_publisher_certificates_container"    ) store = "TrustedPublisher";

     else if (container == "rutoken_certificates_container"              ) store = "Рутокен ЭЦП 2.0";
     else if (container == "jacarta_certificates_container"              ) store = "JaCarta GOST 2.0";
     else if (container == "esmart_certificates_container"               ) store = "ESMARTToken GOST";

     else return;

     let request = {};
     request.store = store;
     request.fields = {};

     _crypto_0db7d9ec48754f9080a77e237238453b.certificates( request, function (message) {

          if (message.error && message.error.length) {
               notifier.notify(message.error);
          } else {
               let result = JSON.parse(message.method.result);

               result.forEach(function (each, index) {
                    let element = createCertificate(container, each);
                    element.setAttribute("id", index.toString());
               });

               updateCertificateActions();
          }
     });
}

function updateCertificateActions() {

     const myCertificateFilter = document.getElementById("find_my_certificate").value;
     let activeMyCertificate = activeCertificate("my_certificates_container");

     if (myCertificateFilter.length) {
          $("#my_certificates_container > .certificate").removeClass('active');
          $("#my_certificates_container > .certificate").hide();

          $("#my_certificates_container > .certificate:contains(" + myCertificateFilter + ")").show();

          if (activeMyCertificate && activeMyCertificate.innerHTML.indexOf(myCertificateFilter) > -1) {
               activeMyCertificate.className += " active";
          } else {
               activeMyCertificate = undefined;
          }
     } else {
          $("#my_certificates_container > .certificate").show();
     }

     const rootCertificateFilter = document.getElementById("find_root_certificate").value;
     let activeRootCertificate = activeCertificate("root_certificates_container");

     if (rootCertificateFilter.length) {
          $("#root_certificates_container > .certificate").removeClass('active');
          $("#root_certificates_container > .certificate").hide();

          $("#root_certificates_container > .certificate:contains(" + rootCertificateFilter + ")").show();

          if (activeRootCertificate && activeRootCertificate.innerHTML.indexOf(rootCertificateFilter) > -1) {
               activeRootCertificate.className += " active";
          } else {
               activeRootCertificate = undefined;
          }
     } else {
          $("#root_certificates_container > .certificate").show();
     }

     const addressBookCertificateFilter = document.getElementById("find_address_book_certificate").value;
     let activeAddressBookCertificate = activeCertificate("address_book_certificates_container");

     if (addressBookCertificateFilter.length) {
          $("#address_book_certificates_container > .certificate").removeClass('active');
          $("#address_book_certificates_container > .certificate").hide();

          $("#address_book_certificates_container > .certificate:contains(" + addressBookCertificateFilter + ")").show();

          if (activeAddressBookCertificate && activeAddressBookCertificate.innerHTML.indexOf(addressBookCertificateFilter) > -1) {
               activeAddressBookCertificate.className += " active";
          } else {
               activeAddressBookCertificate = undefined;
          }
     } else {
          $("#address_book_certificates_container > .certificate").show();
     }

     const authRootCertificateFilter = document.getElementById("find_auth_root_certificate").value;
     let activeAuthRootCertificate = activeCertificate("auth_root_certificates_container");

     if (authRootCertificateFilter.length) {
          $("#auth_root_certificates_container > .certificate").removeClass('active');
          $("#auth_root_certificates_container > .certificate").hide();

          $("#auth_root_certificates_container > .certificate:contains(" + authRootCertificateFilter + ")").show();

          if (activeAuthRootCertificate && activeAuthRootCertificate.innerHTML.indexOf(authRootCertificateFilter) > -1) {
               activeAuthRootCertificate.className += " active";
          } else {
               activeAuthRootCertificate = undefined;
          }
     } else {
          $("#auth_root_certificates_container > .certificate").show();
     }

     const certificateAuthorityCertificateFilter = document.getElementById("find_certificate_authority_certificate").value;
     let activeCertificateAuthorityCertificate = activeCertificate("certificate_authority_certificates_container");

     if (certificateAuthorityCertificateFilter.length) {
          $("#certificate_authority_certificates_container > .certificate").removeClass('active');
          $("#certificate_authority_certificates_container > .certificate").hide();

          $("#certificate_authority_certificates_container > .certificate:contains(" + certificateAuthorityCertificateFilter + ")").show();

          if (activeCertificateAuthorityCertificate && activeCertificateAuthorityCertificate.innerHTML.indexOf(CertificateAuthorityCertificateFilter) > -1) {
               activeCertificateAuthorityCertificate.className += " active";
          } else {
               activeCertificateAuthorityCertificate = undefined;
          }
     } else {
          $("#certificate_authority_certificates_container > .certificate").show();
     }

     const disallowedCertificateFilter = document.getElementById("find_disallowed_certificate").value;
     let activeDisallowedCertificate = activeCertificate("disallowed_certificates_container");

     if (disallowedCertificateFilter.length) {
          $("#disallowed_certificates_container > .certificate").removeClass('active');
          $("#disallowed_certificates_container > .certificate").hide();

          $("#disallowed_certificates_container > .certificate:contains(" + disallowedCertificateFilter + ")").show();

          if (activeDisallowedCertificate && activeDisallowedCertificate.innerHTML.indexOf(disallowedCertificateFilter) > -1) {
               activeDisallowedCertificate.className += " active";
          } else {
               activeDisallowedCertificate = undefined;
          }
     } else {
          $("#disallowed_certificates_container > .certificate").show();
     }

     const trustedPeopleCertificateFilter = document.getElementById("find_trusted_people_certificate").value;
     let activeTrustedPeopleCertificate = activeCertificate("trusted_people_certificates_container");

     if (trustedPeopleCertificateFilter.length) {
          $("#trusted_people_certificates_container > .certificate").removeClass('active');
          $("#trusted_people_certificates_container > .certificate").hide();

          $("#trusted_people_certificates_container > .certificate:contains(" + trustedPeopleCertificateFilter + ")").show();

          if (activeTrustedPeopleCertificate && activeTrustedPeopleCertificate.innerHTML.indexOf(trustedPeopleCertificateFilter) > -1) {
               activeTrustedPeopleCertificate.className += " active";
          } else {
               activeTrustedPeopleCertificate = undefined;
          }
     } else {
          $("#trusted_people_certificates_container > .certificate").show();
     }

     const trustedPublisherCertificateFilter = document.getElementById("find_trusted_publisher_certificate").value;
     let activeTrustedPublisherCertificate = activeCertificate("trusted_publisher_certificates_container");

     if (trustedPublisherCertificateFilter.length) {
          $("#trusted_publisher_certificates_container > .certificate").removeClass('active');
          $("#trusted_publisher_certificates_container > .certificate").hide();

          $("#trusted_publisher_certificates_container > .certificate:contains(" + trustedPublisherCertificateFilter + ")").show();

          if (activeTrustedPublisherCertificate && activeTrustedPublisherCertificate.innerHTML.indexOf(trustedPublisherCertificateFilter) > -1) {
               activeTrustedPublisherCertificate.className += " active";
          } else {
               activexTrustedPublisherCertificate = undefined;
          }
     } else {
          $("#trusted_publisher_certificates_container > .certificate").show();
     }

     const rutokenCertificateFilter = document.getElementById("find_rutoken_certificate").value;
     let activeRuTokenCertificate = activeCertificate("rutoken_certificates_container");

     if (rutokenCertificateFilter.length) {
          $("#rutoken_certificates_container > .certificate").removeClass('active');
          $("#rutoken_certificates_container > .certificate").hide();

          $("#rutoken_certificates_container > .certificate:contains(" + rutokenCertificateFilter + ")").show();

          if (activeRuTokenCertificate && activeRuTokenCertificate.innerHTML.indexOf(rutokenCertificateFilter) > -1) {
               activeRuTokenCertificate.className += " active";
          } else {
               activeRuTokenCertificate = undefined;
          }
     } else {
          $("#rutoken_certificates_container > .certificate").show();
     }

     const jacartaCertificateFilter = document.getElementById("find_jacarta_certificate").value;
     let activeJacartaCertificate = activeCertificate("jacarta_certificates_container");

     if (jacartaCertificateFilter.length) {
          $("#jacarta_certificates_container > .certificate").removeClass('active');
          $("#jacarta_certificates_container > .certificate").hide();

          $("#jacarta_certificates_container > .certificate:contains(" + jacartaCertificateFilter + ")").show();

          if (activeJacartaCertificate && activeJacartaCertificate.innerHTML.indexOf(jacartaCertificateFilter) > -1) {
               activeJacartaCertificate.className += " active";
          } else {
               activeJacartaCertificate = undefined;
          }
     } else {
          $("#jacarta_certificates_container > .certificate").show();
     }

     const esmartCertificateFilter = document.getElementById("find_esmart_certificate").value;
     let activeESmartCertificate = activeCertificate("esmart_certificates_container");

     if (esmartCertificateFilter.length) {
          $("#esmart_certificates_container > .certificate").removeClass('active');
          $("#esmart_certificates_container > .certificate").hide();

          $("#esmart_certificates_container > .certificate:contains(" + esmartCertificateFilter + ")").show();

          if (activeESmartCertificate && activeESmartCertificate.innerHTML.indexOf(esmartCertificateFilter) > -1) {
               activeESmartCertificate.className += " active";
          } else {
               activeESmartCertificate = undefined;
          }
     } else {
          $("#esmart_certificates_container > .certificate").show();
     }

     $("#delete_my_certificate").prop("disabled", !activeMyCertificate);
     $("#save_to_buffer_my_certificate").prop("disabled", !activeMyCertificate);
     $("#sign_file").prop("disabled", !activeMyCertificate);

     $("#save_to_buffer_root_certificate").prop("disabled", !activeRootCertificate);
     $("#save_to_buffer_address_book_certificate").prop("disabled", !activeAddressBookCertificate);
     $("#save_to_buffer_auth_root_certificate").prop("disabled", !activeAuthRootCertificate);
     $("#save_to_buffer_certificate_authority_certificate").prop("disabled", !activeCertificateAuthorityCertificate);
     $("#save_to_buffer_disallowed_certificate").prop("disabled", !activeDisallowedCertificate);
     $("#save_to_buffer_trusted_people_certificate").prop("disabled", !activeTrustedPeopleCertificate);
     $("#save_to_buffer_trusted_publisher_certificate").prop("disabled", !activeTrustedPublisherCertificate); 

     $("#save_to_buffer_rutoken_certificate").prop("disabled", !activeRuTokenCertificate); 
     $("#save_to_buffer_jacarta_certificate").prop("disabled", !activeJacartaCertificate); 
     $("#save_to_buffer_esmart_certificate").prop("disabled", !activeESmartCertificate); 

     document.getElementById("my_certificates_count").innerText = $("#my_certificates_container > .certificate").length;
     document.getElementById("root_certificates_count").innerText = $("#root_certificates_container > .certificate").length;
     document.getElementById("address_book_certificates_count").innerText = $("#address_book_certificates_container > .certificate").length;
     document.getElementById("auth_root_certificates_count").innerText = $("#auth_root_certificates_container > .certificate").length;
     document.getElementById("certificate_authority_certificates_count").innerText = $("#certificate_authority_certificates_container > .certificate").length;
     document.getElementById("disallowed_certificates_count").innerText = $("#disallowed_certificates_container > .certificate").length;
     document.getElementById("trusted_people_certificates_count").innerText = $("#trusted_people_certificates_container > .certificate").length;
     document.getElementById("trusted_publisher_certificates_count").innerText = $("#trusted_publisher_certificates_container > .certificate").length;

     document.getElementById("rutoken_certificates_count").innerText = $("#rutoken_certificates_container > .certificate").length;
     document.getElementById("jacarta_certificates_count").innerText = $("#jacarta_certificates_container > .certificate").length;
     document.getElementById("esmart_certificates_count").innerText = $("#esmart_certificates_container > .certificate").length;
}


function certificateContent(certificate) {
     return certificate.title;
}

function saveToBufferCertificate(certificate) {
     if (!certificate) return;

     var $temp = $("<input>");
     $("body").append($temp);
     $temp.val(certificate.title).select();
     document.execCommand("copy");
     $temp.remove();
}

function saveToBufferElement(certificate) {
     if (!certificate) return;

     var $temp = $("<input>");
     $("body").append($temp);
     $temp.val(certificate.dataset.certificate).select();
     document.execCommand("copy");
     $temp.remove();
};


function deleteCertificate(certificate) {
     if (typeof _crypto_0db7d9ec48754f9080a77e237238453b === "undefined" ) {
          console.warn(`certificate.js: crypto undefined, crypto.deleteCertificate canceled`);
          return;
     }
     
     if (!certificate) return;

     let file = new GosuslugiPluginFileInfo(certificateContent(certificate), "");

     _crypto_0db7d9ec48754f9080a77e237238453b.deleteCertificate(file, function (message) {

          if (message.error && message.error.length) {
               notifier.notify(message.error);
          } else {
               updateCertificates();
          }
     });
}

function addCertificate() {
     if (typeof _crypto_0db7d9ec48754f9080a77e237238453b === "undefined" ) {
          console.warn(`certificate.js: crypto undefined, crypto.addCertificate canceled`);
          return;
     }

     let base64 = prompt("enter certificate in base64:", "");
     let request = {};
     request.certificate = new GosuslugiPluginFileInfo(base64, "");
     request.container = "";

     _crypto_0db7d9ec48754f9080a77e237238453b.addCertificate(request, function (message) {

          if (message.error && message.error.length) {
               notifier.notify(message.error);
          } else {
               updateCertificates();
          }
     });
}

function clearCertificates(container) {
     $("#" + container + " > .certificate").remove();
     updateCertificateActions();
}

function updateCertificates() {

     clearCertificates("my_certificates_container");
     clearCertificates("root_certificates_container");
     clearCertificates("address_book_certificates_container");
     clearCertificates("auth_root_certificates_container");
     clearCertificates("certificate_authority_certificates_container");
     clearCertificates("disallowed_certificates_container");
     clearCertificates("trusted_people_certificates_container");
     clearCertificates("trusted_publisher_certificates_container"); 

     clearCertificates("rutoken_certificates_container"); 
     clearCertificates("jacarta_certificates_container"); 
     clearCertificates("esmart_certificates_container"); 

     appendCertificates("my_certificates_container");
     appendCertificates("root_certificates_container");
     appendCertificates("address_book_certificates_container");
     appendCertificates("auth_root_certificates_container");
     appendCertificates("certificate_authority_certificates_container");
     appendCertificates("disallowed_certificates_container");
     appendCertificates("trusted_people_certificates_container");
     appendCertificates("trusted_publisher_certificates_container");

     appendCertificates("rutoken_certificates_container");
     appendCertificates("jacarta_certificates_container");
     appendCertificates("esmart_certificates_container");
}