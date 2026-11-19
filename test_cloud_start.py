import unittest
from cloud_start import validate_environment

class CloudStartTests(unittest.TestCase):
    def test_missing_database_cannot_fall_back_to_local(self):
        with self.assertRaisesRegex(ValueError, 'DATABASE_URL'):
            validate_environment({'OANDA_ACCOUNT_ID': 'demo', 'OANDA_TOKEN': 'example'})

    def test_invalid_uri_error_does_not_expose_secret(self):
        with self.assertRaises(ValueError) as result:
            validate_environment({'DATABASE_URL': 'https://user:SECRET@example.com',
                                  'OANDA_ACCOUNT_ID': 'demo', 'OANDA_TOKEN': 'example'})
        self.assertNotIn('SECRET', str(result.exception))

    def test_postgres_settings_accepted(self):
        validate_environment({'DATABASE_URL': 'postgresql://user:example@db.example.com/db',
                              'OANDA_ACCOUNT_ID': 'demo', 'OANDA_TOKEN': 'example'})

if __name__ == '__main__': unittest.main()
