const loginForm = document.querySelector(".login-form");
const registerForm = document.querySelector(".register-form");
const forgotPasswordForm = document.querySelector(".forgot-password-form");
const wrapper = document.querySelector(".wrapper");
const loginTitle = document.querySelector(".title-login");
const registerTitle = document.querySelector(".title-register");
const forgotTitle = document.querySelector(".title-forgot");
const signUpBtn = document.querySelector("#SignUpBtn");
const signInBtn = document.querySelector("#SignInBtn");
const termsModal = document.getElementById('termsModal');
const openTermsLink = document.getElementById('openTermsModal');
const closeTermsButton = document.getElementById('closeTermsModal');

// --- Form Switching Functions ---

function loginFunction(){
    if (!loginForm || !registerForm || !forgotPasswordForm || !wrapper || !loginTitle || !registerTitle || !forgotTitle) return; // Safety check
    loginForm.style.left = "50%";
    loginForm.style.opacity = 1;
    registerForm.style.left = "150%";
    registerForm.style.opacity = 0;
    forgotPasswordForm.style.left = "150%";
    forgotPasswordForm.style.opacity = 0;
    wrapper.style.height = "500px";
    loginTitle.style.top = "50%";
    loginTitle.style.opacity = 1;
    registerTitle.style.top = "50px";
    registerTitle.style.opacity = 0;
    forgotTitle.style.top = "50px";
    forgotTitle.style.opacity = 0;

    clearStatusMessages();
    document.getElementById('forgot-step-1').style.display = 'block';
    document.getElementById('forgot-step-2').style.display = 'none';
    document.getElementById('forgot-step-3').style.display = 'none';
    if(forgotPasswordForm) forgotPasswordForm.reset(); // Reset forgot form fields
    sessionStorage.removeItem('resetEmail');
}

function registerFunction(){
    if (!loginForm || !registerForm || !forgotPasswordForm || !wrapper || !loginTitle || !registerTitle || !forgotTitle) return;
    loginForm.style.left = "-50%";
    loginForm.style.opacity = 0;
    registerForm.style.left = "50%";
    registerForm.style.opacity = 1;
    forgotPasswordForm.style.left = "150%";
    forgotPasswordForm.style.opacity = 0;
    wrapper.style.height = "680px"; 
    loginTitle.style.top = "50px";
    loginTitle.style.opacity = 0;
    registerTitle.style.top = "50%";
    registerTitle.style.opacity = 1;
    forgotTitle.style.top = "50px";
    forgotTitle.style.opacity = 0;

    clearStatusMessages();
}

function forgotPasswordFunction() {
    if (!loginForm || !registerForm || !forgotPasswordForm || !wrapper || !loginTitle || !registerTitle || !forgotTitle) return;
    loginForm.style.left = "-50%";
    loginForm.style.opacity = 0;
    registerForm.style.left = "150%";
    registerForm.style.opacity = 0;
    forgotPasswordForm.style.left = "50%";
    forgotPasswordForm.style.opacity = 1;
    wrapper.style.height = "430px"; // Initial height for step 1
    loginTitle.style.top = "50px";
    loginTitle.style.opacity = 0;
    registerTitle.style.top = "50px";
    registerTitle.style.opacity = 0;
    forgotTitle.style.top = "50%";
    forgotTitle.style.opacity = 1;

    document.getElementById('forgot-step-1').style.display = 'block';
    document.getElementById('forgot-step-2').style.display = 'none';
    document.getElementById('forgot-step-3').style.display = 'none';
    if(forgotPasswordForm) forgotPasswordForm.reset();
    sessionStorage.removeItem('resetEmail');

    clearStatusMessages();
}

// --- Password Visibility Toggles ---

var showPasswordLogin = document.getElementById('show-password-log');
var passwordFieldLogin = document.getElementById('password');
var showPasswordRegister = document.getElementById('show-password-reg');
var passwordFieldRegister = document.getElementById('password-reg');
var showPasswordForgot = document.getElementById('show-password-forgot');
var passwordFieldForgot = document.getElementById('new-password');

if (showPasswordLogin && passwordFieldLogin) {
    showPasswordLogin.addEventListener('change', function() {
        passwordFieldLogin.type = this.checked ? 'text' : 'password';
    });
}
if (showPasswordRegister && passwordFieldRegister) {
    showPasswordRegister.addEventListener('change', function() {
        passwordFieldRegister.type = this.checked ? 'text' : 'password';
    });
}
if (showPasswordForgot && passwordFieldForgot) {
    showPasswordForgot.addEventListener('change', function() {
        passwordFieldForgot.type = this.checked ? 'text' : 'password';
    });
}

// --- Status Message Handling ---

function showStatusMessage(formElement, message, isSuccess) {
    if (!formElement) {
        console.error("Attempted to show status message on null form element.");
        return;
    }
    clearStatusMessages(formElement);
    const statusDiv = document.createElement('div');
    statusDiv.className = `status-message ${isSuccess ? 'success' : 'error'}`;
    statusDiv.textContent = message;

    // Try inserting before the .switch-form or specific buttons/elements
    let inserted = false;
    const switchForm = formElement.querySelector('.switch-form');
    if (switchForm) {
        formElement.insertBefore(statusDiv, switchForm);
        inserted = true;
    } else {
         // Try inserting before the last button's container if no switch form
        const lastButton = formElement.querySelector('.btn-submit:last-of-type');
        if (lastButton) {
             const inputBox = lastButton.closest('.input-box');
             if (inputBox && inputBox.parentNode === formElement) { // Ensure it's a direct child container
                 formElement.insertBefore(statusDiv, inputBox.nextSibling); // Insert after the button's container
                 inserted = true;
             }
        }
    }

    // Fallback: append to the form if no suitable location found
    if (!inserted) {
        formElement.appendChild(statusDiv);
    }


    if (isSuccess) {
        setTimeout(() => {
            if (statusDiv.parentNode) {
                statusDiv.remove();
            }
        }, 3500); // Slightly longer timeout
    }
}

function clearStatusMessages(formElement = document) {
    if (!formElement) return;
    const statusMessages = formElement.querySelectorAll('.status-message');
    statusMessages.forEach(msg => msg.remove());
}

// --- Event Listeners for Form Switching ---
document.addEventListener('DOMContentLoaded', () => {
    // Use event delegation on the wrapper for potential dynamic links
    const wrapperElement = document.querySelector('.wrapper');
    if (wrapperElement) {
        wrapperElement.addEventListener('click', (e) => {
            // Check if the click target is the register link
            if (e.target.matches('.login-form .switch-form a') && e.target.onclick === registerFunction) {
                e.preventDefault();
                registerFunction();
            }
            // Check if the click target is the login link
            else if (e.target.matches('.register-form .switch-form a') && e.target.onclick === loginFunction) {
                e.preventDefault();
                loginFunction();
            }
            // Check if the click target is the forgot password link
            else if (e.target.matches('.login-form .col-2 a')) {
                e.preventDefault();
                forgotPasswordFunction();
            }
             // Check if the click target is the back to login link
            else if (e.target.matches('.forgot-password-form .switch-form a') && e.target.onclick === loginFunction) {
                e.preventDefault();
                loginFunction();
            }
        });
    } else {
        console.error("Wrapper element not found for form switch delegation.");
    }
});


// --- Handle login form submission ---
if (loginForm) {
    loginForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(loginForm);

        fetch('/api/login', {
            method: 'POST',
            body: formData,
        })
        .then(response => response.json().then(data => ({ ok: response.ok, status: response.status, data })))
        .then(result => {
            console.log('Login response:', result);
            if (result.ok) {
                showStatusMessage(loginForm, result.data.message, true);
                setTimeout(() => {
                    window.location.href = '/'; // Redirect to Flask index route
                }, 1000);
            } else {
                showStatusMessage(loginForm, result.data.message || `Error: ${result.status}`, false);
            }
        })
        .catch(error => {
            showStatusMessage(loginForm, 'A network or server error occurred. Please try again.', false);
            console.error('Login Fetch Error:', error);
        });
    });
} else {
    console.error("Login form not found.");
}

// --- Handle registration form submission ---
if (registerForm) {
    registerForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const agreeCheckbox = document.getElementById('agree');
        if (!agreeCheckbox || !agreeCheckbox.checked) {
            showStatusMessage(registerForm, 'Please agree to the terms and conditions.', false);
            return;
        }

        const formData = new FormData(registerForm);

        fetch('/api/register', {
            method: 'POST',
            body: formData,
        })
        .then(response => response.json().then(data => ({ ok: response.ok, status: response.status, data })))
        .then(result => {
            console.log('Registration response:', result);
            if (result.ok) {
                showStatusMessage(registerForm, result.data.message, true);
                registerForm.reset();
                setTimeout(() => {
                    loginFunction();
                    // Give login form a moment to appear before showing message
                    setTimeout(() => showStatusMessage(loginForm, 'Registration successful! Please login.', true), 100);
                }, 2000);
            } else {
                showStatusMessage(registerForm, result.data.message || `Error: ${result.status}`, false);
            }
        })
        .catch(error => {
            showStatusMessage(registerForm, 'A network or server error occurred. Please try again.', false);
            console.error('Registration Fetch Error:', error);
        });
    });

    // Remove error state when checkbox is checked
    const agreeCheckbox = document.getElementById('agree');
    if (agreeCheckbox) {
        agreeCheckbox.addEventListener('change', function() {
            if (this.checked) {
                const errorMessages = registerForm.querySelectorAll('.status-message.error');
                errorMessages.forEach(msg => {
                    if (msg.textContent.includes('terms and conditions')) {
                        msg.remove();
                    }
                });
            }
        });
    }
} else {
    console.error("Register form not found.");
}


// --- Forgot Password Steps ---

const verifyEmailBtn = document.getElementById('verifyEmailBtn');
if (verifyEmailBtn && forgotPasswordForm) {
    verifyEmailBtn.addEventListener('click', function() {
        const emailInput = document.getElementById('forgot-email');
        if (!emailInput) return;
        const email = emailInput.value;
        if (!email) {
            showStatusMessage(forgotPasswordForm, 'Please enter your email address.', false);
            return;
        }

        const formData = new FormData();
        formData.append('email', email);
        formData.append('action', 'verifyEmail');

        fetch('/api/forgot-password', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                sessionStorage.setItem('resetEmail', email);
                const questionTextElement = document.getElementById('security-question-text');
                if(questionTextElement) questionTextElement.textContent = data.security_question;
                document.getElementById('forgot-step-1').style.display = 'none';
                document.getElementById('forgot-step-2').style.display = 'block';
                if (wrapper) wrapper.style.height = "500px";
                clearStatusMessages(forgotPasswordForm);
            } else {
                showStatusMessage(forgotPasswordForm, data.message || 'Failed to verify email.', false);
            }
        })
        .catch(error => {
            console.error('Verify Email Error:', error);
            showStatusMessage(forgotPasswordForm, 'An error occurred verifying email. Please try again.', false);
        });
    });
}

const verifyAnswerBtn = document.getElementById('verifyAnswerBtn');
if (verifyAnswerBtn && forgotPasswordForm) {
    verifyAnswerBtn.addEventListener('click', function() {
        const answerInput = document.getElementById('forgot-answer');
        if (!answerInput) return;
        const answer = answerInput.value;
        const email = sessionStorage.getItem('resetEmail');

        if (!answer) { showStatusMessage(forgotPasswordForm, 'Please enter your answer.', false); return; }
        if (!email) { showStatusMessage(forgotPasswordForm, 'Email session lost. Please start over.', false); setTimeout(loginFunction, 2000); return; }

        const formData = new FormData();
        formData.append('email', email);
        formData.append('security_answer', answer);
        formData.append('action', 'verifyAnswer');

        fetch('/api/forgot-password', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                document.getElementById('forgot-step-2').style.display = 'none';
                document.getElementById('forgot-step-3').style.display = 'block';
                 if (wrapper) wrapper.style.height = "450px";
                clearStatusMessages(forgotPasswordForm);
            } else {
                showStatusMessage(forgotPasswordForm, data.message || 'Failed to verify answer.', false);
            }
        })
        .catch(error => {
            console.error('Verify Answer Error:', error);
            showStatusMessage(forgotPasswordForm, 'An error occurred verifying answer. Please try again.', false);
        });
    });
}

const resetPasswordBtn = document.getElementById('resetPasswordBtn');
if (resetPasswordBtn && forgotPasswordForm) {
    resetPasswordBtn.addEventListener('click', function() {
        const newPasswordInput = document.getElementById('new-password');
        if(!newPasswordInput) return;
        const newPassword = newPasswordInput.value;
        const email = sessionStorage.getItem('resetEmail');

        if (!newPassword) { showStatusMessage(forgotPasswordForm, 'Please enter a new password.', false); return; }
        if (!email) { showStatusMessage(forgotPasswordForm, 'Email session lost. Please start over.', false); setTimeout(loginFunction, 2000); return; }

        const formData = new FormData();
        formData.append('email', email);
        formData.append('new_password', newPassword);
        formData.append('action', 'resetPassword');

        fetch('/api/forgot-password', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showStatusMessage(forgotPasswordForm, data.message, true);
                sessionStorage.removeItem('resetEmail');
                if(forgotPasswordForm) forgotPasswordForm.reset(); // Clear the form
                setTimeout(() => {
                    loginFunction();
                    // Give login form a moment to appear before showing message
                    setTimeout(() => showStatusMessage(loginForm, 'Password reset successful! Please login.', true), 100);
                }, 2500); // Slightly longer delay
            } else {
                showStatusMessage(forgotPasswordForm, data.message || 'Failed to reset password.', false);
            }
        })
        .catch(error => {
            console.error('Reset Password Error:', error);
            showStatusMessage(forgotPasswordForm, 'An error occurred resetting password. Please try again.', false);
        });
    });
}

// --- Terms Modal Functionality ---
if (termsModal && openTermsLink && closeTermsButton) {
    function showTermsModal() { termsModal.className = 'terms-modal-visible'; }
    function hideTermsModal() { termsModal.className = 'terms-modal-hidden'; }

    openTermsLink.addEventListener('click', (e) => { e.preventDefault(); showTermsModal(); });
    closeTermsButton.addEventListener('click', hideTermsModal);
    termsModal.addEventListener('click', (e) => { if (e.target === termsModal.querySelector('.terms-modal-overlay')) { hideTermsModal(); } });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && termsModal.className === 'terms-modal-visible') { hideTermsModal(); } });
} else {
    console.warn("Terms modal elements not found, functionality disabled.");
}

/* Initial check on page load is handled by the internal script in login-reg.html
   and index.html respectively. */
console.log("login-reg.js loaded");
