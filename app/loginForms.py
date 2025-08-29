from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, SelectField
from wtforms.validators import DataRequired, Length, Email, EqualTo

class LoginForm(FlaskForm):
    """Form used for user authentication."""
    username = StringField("Usuário", validators=[DataRequired()])
    password = PasswordField("Senha", validators=[DataRequired()])
    remember_me = BooleanField("Lembrar-me")
    submit = SubmitField("Entrar")

class RegistrationForm(FlaskForm):
    """Form used for new user registration."""
    username = StringField('Usuário', validators=[
        DataRequired(),
        Length(min=4, max=20)
    ])
    email = StringField('Email', validators=[
        DataRequired(),
        Email()
    ])
    name = StringField('Nome Completo', validators=[DataRequired()])
    password = PasswordField('Senha', validators=[
        DataRequired(),
        Length(min=6)
    ])
    confirm_password = PasswordField('Confirmar Senha', validators=[
        DataRequired(),
        EqualTo('password')
    ])
    role = SelectField('Perfil', choices=[
        ('user', 'Usuário'),
        ('admin', 'Administrador')
    ], validators=[DataRequired()])

    submit = SubmitField('Cadastrar')
