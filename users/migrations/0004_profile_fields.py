"""
Migration: add all new profile fields
Run with:  python manage.py migrate
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    # ── Change this to your actual last migration name ──
    # Find it with:  python manage.py showmigrations
    dependencies = [
        ('users', '0003_rolepermission'),   # <-- replace 'accounts' with your app name
                                        #     and '0001_initial' with your latest migration
    ]

    operations = [

        # ── 1. Add avatar field to User ──────────────────────
        migrations.AddField(
            model_name='user',
            name='avatar',
            field=models.ImageField(
                blank=True, null=True, upload_to='avatars/'
            ),
        ),

        # ── 2. Add salary_in_hand to SalaryDetails ───────────
        migrations.AddField(
            model_name='salarydetails',
            name='salary_in_hand',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=10
            ),
        ),

        # ── 3. New fields on AdditionalDetails ───────────────
        migrations.AddField(
            model_name='additionaldetails',
            name='personal_email',
            field=models.EmailField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='alternate_phone',
            field=models.CharField(blank=True, max_length=15, null=True),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='gender',
            field=models.CharField(
                blank=True,
                choices=[
                    ('Male',   'Male'),
                    ('Female', 'Female'),
                    ('Other',  'Other'),
                ],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='marital_status',
            field=models.CharField(
                blank=True,
                choices=[
                    ('Single',  'Single'),
                    ('Married', 'Married'),
                ],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='blood_group',
            field=models.CharField(
                blank=True,
                choices=[
                    ('A+', 'A+'), ('A-', 'A-'),
                    ('B+', 'B+'), ('B-', 'B-'),
                    ('AB+','AB+'),('AB-','AB-'),
                    ('O+', 'O+'), ('O-', 'O-'),
                ],
                max_length=5,
                null=True,
            ),
        ),
        # emergency_contact already exists — rename concept:
        # old field was phone number, new field is the person's name
        # We add the two new fields and leave the old one intact
        migrations.AddField(
            model_name='additionaldetails',
            name='emergency_relation',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='emergency_phone',
            field=models.CharField(blank=True, max_length=15, null=True),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='current_address',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='additionaldetails',
            name='permanent_address',
            field=models.TextField(blank=True, null=True),
        ),

        # ── 4. Make BankDetails fields nullable ──────────────
        #    (original had them as non-nullable CharField)
        migrations.AlterField(
            model_name='bankdetails',
            name='bank_name',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AlterField(
            model_name='bankdetails',
            name='account_number',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name='bankdetails',
            name='ifsc_code',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]